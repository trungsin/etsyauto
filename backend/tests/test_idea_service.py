"""Tests for idea_service functions.

Covers:
- upsert_idea: insert new, idempotent on second call (no duplicate row)
- upsert_idea: mutable fields updated on second call
- upsert_idea: status preserved when updating (never overwritten by upsert)
- list_ideas: filter by status, keyword_id, source
- list_ideas sort=velocity: computed correctly from signal timestamps
- reject_idea / mark_drafted: status transitions
- append_signal + latest_signal: ordering
- compute_velocity: math from two signals
- link_to_listing: idempotent
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.design import Design  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.idea import Idea  # noqa: F401
from app.models.idea_signal import IdeaSignal  # noqa: F401
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.services import idea_service


_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _enable_fk(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=_ENGINE)
    Base.metadata.create_all(bind=_ENGINE)
    yield


@pytest.fixture
def db():
    session = _Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# upsert_idea
# ---------------------------------------------------------------------------


def test_upsert_idea_creates_new(db):
    idea, created = idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="NEW1", title="Nice Mug"
    )
    assert created is True
    assert idea.id is not None
    assert idea.title == "Nice Mug"
    assert idea.status == "new"


def test_upsert_idea_idempotent_on_same_key(db):
    idea1, created1 = idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="DUP1", title="First Title"
    )
    idea2, created2 = idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="DUP1", title="Updated Title"
    )
    assert created1 is True
    assert created2 is False
    assert idea1.id == idea2.id  # same row, not a new one


def test_upsert_idea_updates_mutable_fields(db):
    idea_service.upsert_idea(
        db,
        source="etsy_api",
        source_listing_id="UPD1",
        title="Old Title",
        price_cents=1000,
    )
    idea, _ = idea_service.upsert_idea(
        db,
        source="etsy_api",
        source_listing_id="UPD1",
        title="New Title",
        price_cents=1500,
    )
    assert idea.title == "New Title"
    assert idea.price_cents == 1500


def test_upsert_idea_preserves_status(db):
    idea, _ = idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="STAT1", title="Keep Status"
    )
    # Simulate user rejecting the idea
    idea_service.reject_idea(db, idea.id)

    # Miner runs again and upserts with updated fields — status must stay rejected
    refreshed, created = idea_service.upsert_idea(
        db,
        source="etsy_api",
        source_listing_id="STAT1",
        title="Updated Title",
        status="new",  # even if caller passes status, it must be ignored on update
    )
    assert refreshed.status == "rejected"
    assert created is False


def test_upsert_idea_different_sources_create_separate_rows(db):
    _, c1 = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="X1")
    _, c2 = idea_service.upsert_idea(db, source="extension_passive", source_listing_id="X1")
    assert c1 is True
    assert c2 is True


# ---------------------------------------------------------------------------
# get_idea
# ---------------------------------------------------------------------------


def test_get_idea_found(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="GET1")
    fetched = idea_service.get_idea(db, idea.id)
    assert fetched is not None
    assert fetched.id == idea.id


def test_get_idea_not_found(db):
    assert idea_service.get_idea(db, 9999) is None


# ---------------------------------------------------------------------------
# reject_idea / mark_drafted
# ---------------------------------------------------------------------------


def test_reject_idea(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="REJ1")
    rejected = idea_service.reject_idea(db, idea.id)
    assert rejected.status == "rejected"


def test_mark_drafted(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="DRF1")
    drafted = idea_service.mark_drafted(db, idea.id)
    assert drafted.status == "drafted"


def test_reject_idea_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        idea_service.reject_idea(db, 9999)


# ---------------------------------------------------------------------------
# list_ideas — filtering
# ---------------------------------------------------------------------------


def test_list_ideas_filter_by_status(db):
    idea_service.upsert_idea(db, source="etsy_api", source_listing_id="LS1")
    idea2, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="LS2")
    idea_service.reject_idea(db, idea2.id)

    new_ideas = idea_service.list_ideas(db, status="new")
    rejected_ideas = idea_service.list_ideas(db, status="rejected")

    assert len(new_ideas) == 1
    assert new_ideas[0].source_listing_id == "LS1"
    assert len(rejected_ideas) == 1
    assert rejected_ideas[0].source_listing_id == "LS2"


def test_list_ideas_filter_by_source(db):
    idea_service.upsert_idea(db, source="etsy_api", source_listing_id="SRC1")
    idea_service.upsert_idea(db, source="extension_passive", source_listing_id="SRC2")

    etsy_ideas = idea_service.list_ideas(db, source="etsy_api")
    ext_ideas = idea_service.list_ideas(db, source="extension_passive")

    assert len(etsy_ideas) == 1
    assert len(ext_ideas) == 1


def test_list_ideas_filter_by_keyword_id(db):
    from app.models.keyword import Keyword

    kw = Keyword(term="filter-kw")
    db.add(kw)
    db.commit()
    db.refresh(kw)

    idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="KID1", keyword_id=kw.id
    )
    idea_service.upsert_idea(db, source="etsy_api", source_listing_id="KID2")

    result = idea_service.list_ideas(db, keyword_id=kw.id)
    assert len(result) == 1
    assert result[0].source_listing_id == "KID1"


# ---------------------------------------------------------------------------
# append_signal + latest_signal ordering
# ---------------------------------------------------------------------------


def test_append_signal_creates_row(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="SIG1")
    sig = idea_service.append_signal(db, idea.id, num_favorers=5, views_all_time=50)
    assert sig.id is not None
    assert sig.idea_id == idea.id
    assert sig.num_favorers == 5


def test_latest_signal_returns_most_recent(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="SIG2")

    sig1 = idea_service.append_signal(db, idea.id, num_favorers=10, views_all_time=100)
    sig2 = idea_service.append_signal(db, idea.id, num_favorers=20, views_all_time=200)

    # Manually set timestamps to guarantee ordering since SQLite server_default
    # resolution may be 1-second, causing both to have identical ts in fast tests
    sig1.ts = datetime(2026, 1, 1, 12, 0, 0)
    sig2.ts = datetime(2026, 1, 1, 13, 0, 0)
    db.commit()

    latest = idea_service.latest_signal(db, idea.id)
    assert latest is not None
    assert latest.id == sig2.id
    assert latest.num_favorers == 20


def test_latest_signal_no_signals_returns_none(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="NOSIG")
    assert idea_service.latest_signal(db, idea.id) is None


def test_append_signal_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        idea_service.append_signal(db, 9999, num_favorers=1, views_all_time=1)


# ---------------------------------------------------------------------------
# compute_velocity
# ---------------------------------------------------------------------------


def test_compute_velocity_correct_math(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VEL1")
    sig1 = idea_service.append_signal(db, idea.id, num_favorers=100, views_all_time=None)
    sig2 = idea_service.append_signal(db, idea.id, num_favorers=110, views_all_time=None)

    # Set timestamps 2 hours apart
    sig1.ts = datetime(2026, 1, 1, 10, 0, 0)
    sig2.ts = datetime(2026, 1, 1, 12, 0, 0)
    db.commit()

    velocity = idea_service.compute_velocity(db, idea.id)
    # 10 favorers / 2 hours = 5.0 favorers/hour
    assert abs(velocity - 5.0) < 0.001


def test_compute_velocity_no_signals_returns_zero(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VEL2")
    assert idea_service.compute_velocity(db, idea.id) == 0.0


def test_compute_velocity_single_signal_returns_zero(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VEL3")
    idea_service.append_signal(db, idea.id, num_favorers=50, views_all_time=None)
    assert idea_service.compute_velocity(db, idea.id) == 0.0


def test_compute_velocity_null_favorers_returns_zero(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VEL4")
    sig1 = idea_service.append_signal(db, idea.id, num_favorers=None, views_all_time=None)
    sig2 = idea_service.append_signal(db, idea.id, num_favorers=None, views_all_time=None)
    sig1.ts = datetime(2026, 1, 1, 10, 0, 0)
    sig2.ts = datetime(2026, 1, 1, 12, 0, 0)
    db.commit()
    assert idea_service.compute_velocity(db, idea.id) == 0.0


# ---------------------------------------------------------------------------
# list_ideas sort=velocity
# ---------------------------------------------------------------------------


def test_list_ideas_sort_velocity(db):
    """Ideas with higher velocity should appear first."""
    slow, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VSLW")
    fast, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="VFST")

    # Slow idea: +2 favorers over 2 hours = 1/hr
    s1 = idea_service.append_signal(db, slow.id, num_favorers=100, views_all_time=None)
    s2 = idea_service.append_signal(db, slow.id, num_favorers=102, views_all_time=None)
    s1.ts = datetime(2026, 1, 1, 10, 0, 0)
    s2.ts = datetime(2026, 1, 1, 12, 0, 0)

    # Fast idea: +20 favorers over 2 hours = 10/hr
    f1 = idea_service.append_signal(db, fast.id, num_favorers=50, views_all_time=None)
    f2 = idea_service.append_signal(db, fast.id, num_favorers=70, views_all_time=None)
    f1.ts = datetime(2026, 1, 1, 10, 0, 0)
    f2.ts = datetime(2026, 1, 1, 12, 0, 0)

    db.commit()

    results = idea_service.list_ideas(db, sort="velocity")
    assert len(results) == 2
    assert results[0].id == fast.id
    assert results[1].id == slow.id


# ---------------------------------------------------------------------------
# link_to_listing
# ---------------------------------------------------------------------------


def test_link_to_listing_creates_row(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="LTL1")
    listing = Listing(etsy_listing_id="ETL_LTL1", original_title="Link Test")
    db.add(listing)
    db.commit()
    db.refresh(listing)

    link = idea_service.link_to_listing(db, idea.id, listing.id)
    assert link.idea_id == idea.id
    assert link.listing_id == listing.id


def test_link_to_listing_idempotent(db):
    idea, _ = idea_service.upsert_idea(db, source="etsy_api", source_listing_id="LTL2")
    listing = Listing(etsy_listing_id="ETL_LTL2", original_title="Idempotent Test")
    db.add(listing)
    db.commit()
    db.refresh(listing)

    link1 = idea_service.link_to_listing(db, idea.id, listing.id)
    link2 = idea_service.link_to_listing(db, idea.id, listing.id)
    assert link1.idea_id == link2.idea_id
    assert link1.listing_id == link2.listing_id


def test_link_to_listing_idea_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        idea_service.link_to_listing(db, 9999, 1)
