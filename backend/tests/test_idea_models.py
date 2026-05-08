"""Tests for Keyword, Idea, IdeaSignal, and IdeaToListing models.

Covers:
- Basic row creation and retrieval
- FK cascade behaviour (idea delete → signals deleted)
- FK SET NULL behaviour (keyword delete → idea.keyword_id becomes NULL)
- Composite unique constraint on (source, source_listing_id)
- Composite PK uniqueness on idea_to_listing
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base

# Import models so their tables are registered on Base.metadata
from app.models.design import Design  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.idea import Idea  # noqa: F401
from app.models.idea_signal import IdeaSignal  # noqa: F401
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
from app.models.listing import Listing  # noqa: F401


_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _enable_foreign_keys(dbapi_conn, _record):
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
# Keyword
# ---------------------------------------------------------------------------


def test_create_keyword(db):
    kw = Keyword(term="wall art")
    db.add(kw)
    db.commit()
    db.refresh(kw)
    assert kw.id is not None
    assert kw.term == "wall art"
    assert kw.enabled is True
    assert kw.last_run_at is None
    assert kw.created_at is not None


def test_keyword_term_unique_constraint(db):
    db.add(Keyword(term="unique-term"))
    db.commit()
    db.add(Keyword(term="unique-term"))
    with pytest.raises(IntegrityError):
        db.commit()


# ---------------------------------------------------------------------------
# Idea
# ---------------------------------------------------------------------------


def test_create_idea_minimal(db):
    idea = Idea(source="etsy_api", source_listing_id="ABC123")
    db.add(idea)
    db.commit()
    db.refresh(idea)
    assert idea.id is not None
    assert idea.status == "new"
    assert idea.keyword_id is None


def test_create_idea_with_keyword(db):
    kw = Keyword(term="mug design")
    db.add(kw)
    db.commit()
    db.refresh(kw)

    idea = Idea(source="etsy_api", source_listing_id="MUG1", keyword_id=kw.id, title="Nice Mug")
    db.add(idea)
    db.commit()
    db.refresh(idea)
    assert idea.keyword_id == kw.id


def test_idea_source_pair_unique_constraint(db):
    db.add(Idea(source="etsy_api", source_listing_id="DUP1"))
    db.commit()
    db.add(Idea(source="etsy_api", source_listing_id="DUP1"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_idea_source_pair_different_sources_allowed(db):
    db.add(Idea(source="etsy_api", source_listing_id="SHARED1"))
    db.add(Idea(source="extension_passive", source_listing_id="SHARED1"))
    db.commit()  # no error — different source
    from sqlalchemy import select
    count = db.scalar(select(Idea))
    assert count is not None


# ---------------------------------------------------------------------------
# IdeaSignal cascade delete
# ---------------------------------------------------------------------------


def test_idea_delete_cascades_signals(db):
    idea = Idea(source="etsy_api", source_listing_id="CASCADE1")
    db.add(idea)
    db.commit()
    db.refresh(idea)

    sig = IdeaSignal(idea_id=idea.id, num_favorers=10, views_all_time=100)
    db.add(sig)
    db.commit()

    # Delete the parent idea
    db.delete(idea)
    db.commit()

    from sqlalchemy import select
    remaining = db.scalars(select(IdeaSignal).where(IdeaSignal.idea_id == idea.id)).all()
    assert remaining == []


# ---------------------------------------------------------------------------
# Keyword delete → SET NULL on idea.keyword_id
# ---------------------------------------------------------------------------


def test_keyword_delete_sets_null_on_idea(db):
    kw = Keyword(term="setnull-test")
    db.add(kw)
    db.commit()
    db.refresh(kw)
    kid = kw.id

    idea = Idea(source="etsy_api", source_listing_id="SETNULL1", keyword_id=kid)
    db.add(idea)
    db.commit()
    db.refresh(idea)
    iid = idea.id

    db.delete(kw)
    db.commit()

    db.expire_all()
    refreshed = db.get(Idea, iid)
    assert refreshed is not None
    assert refreshed.keyword_id is None


# ---------------------------------------------------------------------------
# IdeaToListing
# ---------------------------------------------------------------------------


def test_idea_to_listing_composite_pk(db):
    idea = Idea(source="etsy_api", source_listing_id="ITL1")
    db.add(idea)
    db.commit()
    db.refresh(idea)

    listing = Listing(etsy_listing_id="ETL1", original_title="Test Listing")
    db.add(listing)
    db.commit()
    db.refresh(listing)

    link = IdeaToListing(idea_id=idea.id, listing_id=listing.id)
    db.add(link)
    db.commit()

    # Duplicate should raise IntegrityError
    db.add(IdeaToListing(idea_id=idea.id, listing_id=listing.id))
    with pytest.raises(IntegrityError):
        db.commit()


def test_idea_to_listing_cascade_on_idea_delete(db):
    idea = Idea(source="etsy_api", source_listing_id="ITL_CASCADE")
    db.add(idea)
    db.commit()
    db.refresh(idea)

    listing = Listing(etsy_listing_id="ETL_CASCADE", original_title="Cascade Test")
    db.add(listing)
    db.commit()
    db.refresh(listing)

    link = IdeaToListing(idea_id=idea.id, listing_id=listing.id)
    db.add(link)
    db.commit()

    db.delete(idea)
    db.commit()

    from sqlalchemy import select
    remaining = db.scalars(
        select(IdeaToListing).where(IdeaToListing.idea_id == idea.id)
    ).all()
    assert remaining == []
