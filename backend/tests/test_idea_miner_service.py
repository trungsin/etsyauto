"""Tests for idea_miner_service — orchestrator that mines Etsy listings into ideas.

Covers:
- run_for_keyword: happy path upserts 5 ideas + 5 signals (dry-run happy fixture)
- run_for_keyword: idempotent — second run upserts 0 net new ideas, appends 5 more signals
- run_for_keyword: disabled keyword returns zeros without mining
- run_for_keyword: not-found keyword returns zeros
- run_for_keyword: one malformed listing is skipped (errors=1); others still processed
- run_all: processes enabled keywords, skips disabled
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.etsy_public_client import EtsyPublicClient
from app.clients.etsy_public_dry_run_fixtures import SEARCH_RESULTS, LISTING_DETAILS
from app.config import settings
from app.database import Base
from app.models.design import Design  # noqa: F401  (ensures table created)
from app.models.idea import Idea
from app.models.idea_signal import IdeaSignal
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
from app.models.keyword import Keyword
from app.models.listing import Listing  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.services import idea_miner_service, idea_service, keyword_service

# ---------------------------------------------------------------------------
# In-memory SQLite DB shared across this module
# ---------------------------------------------------------------------------

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


@pytest.fixture
def dry_run_on(monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    yield


@pytest.fixture
def dry_run_client(monkeypatch):
    """EtsyPublicClient instance operating in dry-run happy mode."""
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    with patch("app.clients.etsy_public_client.time.sleep"):
        client = EtsyPublicClient(api_key="test")
        yield client
        client.close()


# ---------------------------------------------------------------------------
# Happy path: upserts 5 ideas and 5 signals on first run
# ---------------------------------------------------------------------------


def test_run_for_keyword_happy_upserts_five_ideas_and_signals(db, dry_run_client):
    kw = keyword_service.create_keyword(db, "botanical print")

    with patch("app.services.idea_miner_service.time.sleep"):
        result = idea_miner_service.run_for_keyword(db, kw.id, client=dry_run_client)

    assert result["keyword"] == "botanical print"
    assert result["ideas_upserted"] == 5
    assert result["signals_appended"] == 5
    assert result["errors"] == 0

    ideas = idea_service.list_ideas(db, keyword_id=kw.id)
    assert len(ideas) == 5

    # Verify signals exist for each idea
    for idea in ideas:
        sig = idea_service.latest_signal(db, idea.id)
        assert sig is not None


# ---------------------------------------------------------------------------
# Idempotency: second run upserts 0 new ideas, appends 5 more signals
# ---------------------------------------------------------------------------


def test_run_for_keyword_idempotent_second_run(db, dry_run_client):
    kw = keyword_service.create_keyword(db, "moon phase print")

    with patch("app.services.idea_miner_service.time.sleep"):
        result1 = idea_miner_service.run_for_keyword(db, kw.id, client=dry_run_client)
        result2 = idea_miner_service.run_for_keyword(db, kw.id, client=dry_run_client)

    # Ideas: same 5 exist (idempotent upsert — no duplicates)
    ideas = idea_service.list_ideas(db, keyword_id=kw.id)
    assert len(ideas) == 5

    # Both runs report 5 "upserted" (upsert counts the operation, not net new rows)
    assert result1["ideas_upserted"] == 5
    assert result2["ideas_upserted"] == 5

    # Signals grow — each run appends a new row per idea (timeseries)
    from sqlalchemy import select
    total_signals = len(list(db.scalars(select(IdeaSignal)).all()))
    assert total_signals == 10  # 5 from run1 + 5 from run2


# ---------------------------------------------------------------------------
# Disabled keyword: skipped with zeros
# ---------------------------------------------------------------------------


def test_run_for_keyword_disabled_keyword_returns_zeros(db, dry_run_client):
    kw = keyword_service.create_keyword(db, "disabled term")
    keyword_service.toggle_keyword(db, kw.id, enabled=False)

    result = idea_miner_service.run_for_keyword(db, kw.id, client=dry_run_client)

    assert result["ideas_upserted"] == 0
    assert result["signals_appended"] == 0
    assert result["errors"] == 0
    # No ideas created
    assert idea_service.list_ideas(db, keyword_id=kw.id) == []


# ---------------------------------------------------------------------------
# Not-found keyword: returns zeros gracefully
# ---------------------------------------------------------------------------


def test_run_for_keyword_not_found_returns_zeros(db, dry_run_client):
    result = idea_miner_service.run_for_keyword(db, 99999, client=dry_run_client)
    assert result["keyword"] is None
    assert result["ideas_upserted"] == 0
    assert result["errors"] == 0


# ---------------------------------------------------------------------------
# One bad listing: skipped with errors=1; others still processed
# ---------------------------------------------------------------------------


def test_run_for_keyword_one_bad_listing_skipped(db, monkeypatch):
    """A get_listing error on one listing increments errors; others still upserted."""
    monkeypatch.setattr(settings, "etsy_dry_run", False)

    kw = keyword_service.create_keyword(db, "wall art")

    # Build a mock client: search returns 5 summaries, get_listing fails on second call
    mock_client = MagicMock(spec=EtsyPublicClient)
    mock_client.search_active_listings.return_value = SEARCH_RESULTS  # 5 items

    call_count = 0

    def _get_listing(listing_id):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("Simulated bad response for listing")
        # Return fixture detail for the given id
        lid = str(listing_id)
        return LISTING_DETAILS.get(lid, LISTING_DETAILS["9001"])

    mock_client.get_listing.side_effect = _get_listing

    with patch("app.services.idea_miner_service.time.sleep"):
        result = idea_miner_service.run_for_keyword(db, kw.id, client=mock_client)

    assert result["errors"] == 1
    assert result["ideas_upserted"] == 4  # 5 - 1 bad
    assert result["signals_appended"] == 4


# ---------------------------------------------------------------------------
# Top-N filter: many new candidates → only top-N by favorers kept
# ---------------------------------------------------------------------------


def test_run_for_keyword_top_n_filter_keeps_high_favorers(db, monkeypatch):
    """When new candidates exceed keep_top_n_per_run, low-favorers ones are dropped."""
    monkeypatch.setattr(settings, "etsy_dry_run", False)
    monkeypatch.setattr(settings, "etsy_miner_keep_top_n_per_run", 2)

    kw = keyword_service.create_keyword(db, "trending tee")

    # 5 listings: favorers 5, 200, 10, 500, 30 → top 2 by favorers = 500, 200
    summaries = [{"listing_id": str(i)} for i in range(101, 106)]
    favs_by_lid = {"101": 5, "102": 200, "103": 10, "104": 500, "105": 30}

    def _detail(lid):
        return {
            "listing_id": int(lid),
            "title": f"Listing {lid}",
            "num_favorers": favs_by_lid[str(lid)],
            "views_all_time": 100,
            "price": {"amount": 1900, "divisor": 100},
        }

    mock_client = MagicMock(spec=EtsyPublicClient)
    mock_client.search_active_listings.return_value = summaries
    mock_client.get_listing.side_effect = lambda lid: _detail(lid)

    with patch("app.services.idea_miner_service.time.sleep"):
        result = idea_miner_service.run_for_keyword(db, kw.id, client=mock_client)

    assert result["ideas_upserted"] == 2  # only top-2 by favorers
    kept_favs = sorted(
        idea_service.latest_signal(db, i.id).num_favorers
        for i in idea_service.list_ideas(db, keyword_id=kw.id)
    )
    assert kept_favs == [200, 500]


def test_run_for_keyword_top_n_existing_always_upserted(db, monkeypatch):
    """Existing ideas in DB are always re-upserted, regardless of top-N cap on new candidates."""
    monkeypatch.setattr(settings, "etsy_dry_run", False)
    monkeypatch.setattr(settings, "etsy_miner_keep_top_n_per_run", 1)

    kw = keyword_service.create_keyword(db, "established term")

    # Pre-seed an existing idea (low favorers — would normally be filtered out)
    idea_service.upsert_idea(
        db, source="etsy_api", source_listing_id="999",
        title="Already Tracked", keyword_id=kw.id,
    )

    summaries = [
        {"listing_id": "999"},  # existing — should always be processed
        {"listing_id": "888"},  # new, high-fav — kept by top-1
        {"listing_id": "777"},  # new, low-fav — dropped
    ]
    favs = {"999": 2, "888": 800, "777": 5}

    mock_client = MagicMock(spec=EtsyPublicClient)
    mock_client.search_active_listings.return_value = summaries
    mock_client.get_listing.side_effect = lambda lid: {
        "listing_id": int(lid),
        "title": f"L{lid}",
        "num_favorers": favs[str(lid)],
        "views_all_time": 50,
        "price": {"amount": 1900, "divisor": 100},
    }

    with patch("app.services.idea_miner_service.time.sleep"):
        result = idea_miner_service.run_for_keyword(db, kw.id, client=mock_client)

    # 999 (existing) + 888 (top-1 new) = 2 upserted; 777 dropped
    assert result["ideas_upserted"] == 2
    kept = {i.source_listing_id for i in idea_service.list_ideas(db, keyword_id=kw.id)}
    assert kept == {"999", "888"}


# ---------------------------------------------------------------------------
# run_all: respects enabled flag
# ---------------------------------------------------------------------------


def test_run_all_processes_enabled_skips_disabled(db, dry_run_client, monkeypatch):
    """run_all mines enabled keywords and skips disabled ones."""
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")

    kw_enabled = keyword_service.create_keyword(db, "floral print")
    kw_disabled = keyword_service.create_keyword(db, "hidden term")
    keyword_service.toggle_keyword(db, kw_disabled.id, enabled=False)

    with patch("app.services.idea_miner_service.time.sleep"):
        with patch("app.services.idea_miner_service.EtsyPublicClient", return_value=dry_run_client):
            idea_miner_service.run_all(db=db)

    # Enabled keyword should have ideas
    enabled_ideas = idea_service.list_ideas(db, keyword_id=kw_enabled.id)
    assert len(enabled_ideas) == 5

    # Disabled keyword should have no ideas
    disabled_ideas = idea_service.list_ideas(db, keyword_id=kw_disabled.id)
    assert len(disabled_ideas) == 0


# ---------------------------------------------------------------------------
# _map_etsy_to_idea: price parsing edge cases
# ---------------------------------------------------------------------------


def test_map_etsy_to_idea_handles_missing_price(db):
    """_map_etsy_to_idea returns price_cents=None when price key is absent."""
    detail = {
        "listing_id": 1,
        "title": "No Price Listing",
        "tags": [],
        "materials": [],
        "images": [],
    }
    mapped = idea_miner_service._map_etsy_to_idea(detail, keyword_id=1)
    assert mapped["price_cents"] is None
    assert mapped["source"] == "etsy_api"
    assert mapped["source_listing_id"] == "1"


def test_map_etsy_to_idea_parses_price_correctly():
    """_map_etsy_to_idea converts Etsy {amount, divisor} to cents."""
    detail = {
        "listing_id": 42,
        "title": "Test",
        "tags": ["a"],
        "materials": ["b"],
        "images": [{"url_fullxfull": "https://example.com/img.jpg"}],
        "price": {"amount": 1499, "divisor": 100, "currency_code": "USD"},
    }
    mapped = idea_miner_service._map_etsy_to_idea(detail, keyword_id=1)
    assert mapped["price_cents"] == 1499
    assert mapped["reference_image_url"] == "https://example.com/img.jpg"
