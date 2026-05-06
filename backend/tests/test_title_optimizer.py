"""Tests for the Gemini title optimizer worker (Phase 4).

All tests mock google.genai.Client so no real API calls are made.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.listing import Listing
from app.models.title_variant import TitleVariant  # noqa: F401 — register table

# ---------------------------------------------------------------------------
# Shared in-memory DB
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(_TEST_DB_URL, connect_args={"check_same_thread": False})
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def session():
    db = _Session()
    yield db
    db.close()


def _make_listing(session, status: str = "new") -> Listing:
    listing = Listing(
        etsy_listing_id="test-etsy-001",
        original_title="Handmade Ceramic Mug Blue",
        original_desc="Beautiful handmade mug",
        original_tags=json.dumps(["ceramic", "handmade", "mug", "blue", "gift"]),
        status=status,
    )
    session.add(listing)
    session.commit()
    session.refresh(listing)
    return listing


def _make_canned_response(variants: list[dict]) -> MagicMock:
    """Build a mock google-genai response object with given variants."""
    usage = MagicMock()
    usage.prompt_token_count = 300
    usage.candidates_token_count = 200

    response = MagicMock()
    response.text = json.dumps({"variants": variants})
    response.usage_metadata = usage
    return response


_CANNED_VARIANTS = [
    {
        "text": "Handmade Ceramic Mug, Blue Coffee Cup, Pottery Gift",
        "char_count": 51,
        "rationale": "Leads with material + form",
        "target_keywords": ["handmade ceramic mug", "pottery gift"],
    },
    {
        "text": "Blue Pottery Mug, Handmade Stoneware Cup, Unique Kitchen Gift",
        "char_count": 61,
        "rationale": "Leads with colour",
        "target_keywords": ["blue pottery mug", "stoneware cup"],
    },
    {
        "text": "Ceramic Coffee Mug, Artisan Blue Cup, Handmade Kitchen Decor",
        "char_count": 60,
        "rationale": "Leads with use-case",
        "target_keywords": ["ceramic coffee mug", "artisan cup"],
    },
]


# ---------------------------------------------------------------------------
# Test 1: Happy path — 3 variants saved, status transitions correct
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_run_job_saves_variants_and_transitions_status(self, session, monkeypatch):
        # Test mockup-enabled flow: status should advance to 'mockup-pending'
        monkeypatch.setattr("app.services.listing_service.settings.enable_mockup", True)
        listing = _make_listing(session)
        listing_id = listing.id

        canned_resp = _make_canned_response(_CANNED_VARIANTS)

        with patch("app.clients.gemini_text_client.genai.Client") as MockGenai:
            MockGenai.return_value.models.generate_content.return_value = canned_resp

            # Patch SessionLocal to use our test session factory
            with patch("app.workers.title_optimizer.SessionLocal", return_value=session):
                from app.workers.title_optimizer import run_title_optimizer_job
                run_title_optimizer_job()

        # Refresh from DB
        session.expire_all()
        updated = session.get(Listing, listing_id)
        assert updated.status == "mockup-pending"

        variants = session.query(TitleVariant).filter_by(listing_id=listing_id).all()
        assert len(variants) == 3
        for v in variants:
            assert v.char_count == len(v.text)
            assert v.char_count <= 140
            assert v.model == "gemini-2.5-flash"
            assert v.prompt_version == "v1"


# ---------------------------------------------------------------------------
# Test 2: Char limit — variants >140 chars are truncated at last comma
# ---------------------------------------------------------------------------

class TestCharLimitTruncation:
    def test_over_limit_variant_is_truncated(self):
        from app.clients.gemini_text_client import _validate_variants, _truncate_to_limit

        long_text = "A" * 100 + ", " + "B" * 50  # 152 chars
        variants = [{"text": long_text, "char_count": len(long_text), "rationale": "test"}]

        result = _validate_variants(variants)
        assert len(result) == 1
        assert result[0]["char_count"] <= 140
        assert result[0]["char_count"] == len(result[0]["text"])

    def test_truncate_prefers_last_comma(self):
        from app.clients.gemini_text_client import _truncate_to_limit

        text = "Keyword One, Keyword Two, " + "X" * 120  # well over 140
        result = _truncate_to_limit(text)
        assert len(result) <= 140
        # Should cut at a comma boundary
        assert not result.endswith(",")

    def test_exactly_140_chars_passes_unchanged(self):
        from app.clients.gemini_text_client import _validate_variants

        text = "A" * 140
        variants = [{"text": text, "char_count": 140, "rationale": "exact"}]
        result = _validate_variants(variants)
        assert result[0]["text"] == text
        assert result[0]["char_count"] == 140


# ---------------------------------------------------------------------------
# Test 3: Idempotency — running job twice does not create duplicate variants
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_no_duplicate_variants_on_second_run(self, session):
        listing = _make_listing(session)
        listing_id = listing.id

        canned_resp = _make_canned_response(_CANNED_VARIANTS)

        with patch("app.clients.gemini_text_client.genai.Client") as MockGenai:
            MockGenai.return_value.models.generate_content.return_value = canned_resp

            with patch("app.workers.title_optimizer.SessionLocal", return_value=session):
                from app.workers.title_optimizer import run_title_optimizer_job
                run_title_optimizer_job()

        # Manually reset status to 'new' to simulate second run attempt
        listing.status = "new"
        session.commit()

        # Run again — variants already exist, idempotency guard should skip insert
        with patch("app.clients.gemini_text_client.genai.Client") as MockGenai2:
            MockGenai2.return_value.models.generate_content.return_value = canned_resp

            with patch("app.workers.title_optimizer.SessionLocal", return_value=session):
                run_title_optimizer_job()

        session.expire_all()
        count = session.query(TitleVariant).filter_by(listing_id=listing_id).count()
        assert count == 3  # still 3, not 6


# ---------------------------------------------------------------------------
# Test 4: Atomic claim — listing already claimed is skipped
# ---------------------------------------------------------------------------

class TestAtomicClaim:
    def test_already_processing_listing_is_skipped(self, session):
        """If listing is already in title-processing status, worker must not claim it."""
        listing = _make_listing(session, status="title-processing")
        listing_id = listing.id

        from app.services.listing_service import mark_title_processing

        # Attempt to claim — should return False since status != 'new'
        claimed = mark_title_processing(session, listing_id)
        assert claimed is False

        # Status unchanged
        session.expire_all()
        unchanged = session.get(Listing, listing_id)
        assert unchanged.status == "title-processing"

    def test_new_listing_is_claimed_exactly_once(self, session):
        """Simulates two workers racing: only the first UPDATE wins."""
        listing = _make_listing(session, status="new")
        listing_id = listing.id

        from app.services.listing_service import mark_title_processing

        result1 = mark_title_processing(session, listing_id)
        result2 = mark_title_processing(session, listing_id)

        assert result1 is True
        assert result2 is False

        session.expire_all()
        claimed = session.get(Listing, listing_id)
        assert claimed.status == "title-processing"
