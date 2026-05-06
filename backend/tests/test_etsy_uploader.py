"""Tests for Phase 7: Etsy cron uploader with retry logic.

All external I/O (EtsyApiClient, NotionClient, DB session) is mocked.
Uses in-memory SQLite so no real DB is required.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant
from app.services.retry_policy import next_retry_delay, should_retry

# ---------------------------------------------------------------------------
# In-memory DB setup
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


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_listing(session, status="approved", notion_page_id="page-001") -> Listing:
    listing = Listing(
        etsy_listing_id="99887766",
        original_title="Handmade Wool Scarf",
        original_desc="Warm and cozy",
        original_tags=json.dumps(["wool", "scarf"]),
        original_images=json.dumps(["https://example.com/img.jpg"]),
        status=status,
        notion_page_id=notion_page_id,
        push_attempts=0,
    )
    session.add(listing)
    session.commit()
    session.refresh(listing)
    return listing


def _add_selected_title(session, listing_id: int, text="Cozy Wool Scarf — Handmade") -> TitleVariant:
    variant = TitleVariant(
        listing_id=listing_id,
        text=text,
        char_count=len(text),
        model="claude-sonnet-4-6",
        selected=True,
    )
    session.add(variant)
    session.commit()
    session.refresh(variant)
    return variant


def _add_selected_mockup(session, listing_id: int, path="/tmp/mockup.png") -> MockupVariant:
    variant = MockupVariant(
        listing_id=listing_id,
        source_image_url="https://example.com/src.jpg",
        final_image_path=path,
        final_image_url="https://r2.example.com/mockup.png",
        selected=True,
    )
    session.add(variant)
    session.commit()
    session.refresh(variant)
    return variant


# ---------------------------------------------------------------------------
# Tests: retry_policy helpers
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_next_retry_delay_attempt_0(self):
        assert next_retry_delay(0) == 5

    def test_next_retry_delay_attempt_1(self):
        assert next_retry_delay(1) == 30

    def test_next_retry_delay_attempt_2(self):
        assert next_retry_delay(2) == 300

    def test_next_retry_delay_capped_at_max(self):
        assert next_retry_delay(99) == 300

    def test_should_retry_below_max(self):
        assert should_retry(1, max_attempts=3) is True
        assert should_retry(2, max_attempts=3) is True

    def test_should_retry_at_max(self):
        assert should_retry(3, max_attempts=3) is False

    def test_should_retry_above_max(self):
        assert should_retry(5, max_attempts=3) is False


# ---------------------------------------------------------------------------
# Tests: etsy_uploader worker
# ---------------------------------------------------------------------------

class TestEtsyUploaderHappyPath:
    def test_happy_path_pushes_listing(self, session):
        """Approved listing with selected variants → pushed, Notion updated."""
        listing = _make_listing(session, status="approved")
        _add_selected_title(session, listing.id)
        _add_selected_mockup(session, listing.id, path="/tmp/mockup.png")
        listing_id = listing.id

        mock_etsy = MagicMock()
        mock_notion_instance = MagicMock()

        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy, \
             patch("app.workers.etsy_uploader.NotionClient", return_value=mock_notion_instance):

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["listing_id"] == listing_id
        assert result["status"] == "pushed"
        assert result["error"] is None

        session.expire_all()
        updated = session.get(Listing, listing_id)
        assert updated.status == "pushed"
        assert updated.pushed_at is not None
        assert updated.last_push_error is None

        mock_etsy.update_listing.assert_called_once_with("99887766", title="Cozy Wool Scarf — Handmade")
        mock_etsy.upload_listing_image.assert_called_once_with("99887766", "/tmp/mockup.png", rank=1)
        mock_notion_instance.update_page_status.assert_called_once_with("page-001", "Pushed")

    def test_idle_when_no_approved_listings(self, session):
        """No approved listings → returns idle result, no API calls."""
        _make_listing(session, status="review")

        mock_etsy = MagicMock()
        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy:

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["listing_id"] is None
        assert result["status"] == "idle"
        mock_etsy.update_listing.assert_not_called()


class TestEtsyUploaderRetryLogic:
    def test_first_failure_reverts_to_approved(self, session):
        """On first API failure: push_attempts=1, status reverts to 'approved' for retry."""
        listing = _make_listing(session, status="approved")
        _add_selected_title(session, listing.id)
        _add_selected_mockup(session, listing.id)
        listing_id = listing.id

        mock_etsy = MagicMock()
        mock_etsy.update_listing.side_effect = RuntimeError("Etsy 503 Service Unavailable")

        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy, \
             patch("app.workers.etsy_uploader.NotionClient"):

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["status"] == "approved"
        assert "503" in result["error"]

        session.expire_all()
        updated = session.get(Listing, listing_id)
        assert updated.status == "approved"
        assert updated.push_attempts == 1
        assert updated.last_push_error is not None

    def test_max_retries_reached_marks_failed(self, session):
        """After 3 failed attempts, listing is marked 'failed'."""
        listing = _make_listing(session, status="approved")
        listing.push_attempts = 2  # simulate 2 prior failures
        session.commit()
        _add_selected_title(session, listing.id)
        _add_selected_mockup(session, listing.id)
        listing_id = listing.id

        mock_etsy = MagicMock()
        mock_etsy.update_listing.side_effect = RuntimeError("Persistent error")
        mock_notion_instance = MagicMock()

        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy, \
             patch("app.workers.etsy_uploader.NotionClient", return_value=mock_notion_instance):

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["status"] == "failed"

        session.expire_all()
        updated = session.get(Listing, listing_id)
        assert updated.status == "failed"
        assert updated.push_attempts == 3
        assert updated.last_push_error is not None
        mock_notion_instance.update_page_status.assert_called_once_with("page-001", "Failed")

    def test_idempotency_pushed_listing_not_reprocessed(self, session):
        """Listing already in status='pushed' is ignored (not 'approved')."""
        _make_listing(session, status="pushed")

        mock_etsy = MagicMock()
        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy:

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["status"] == "idle"
        mock_etsy.update_listing.assert_not_called()

    def test_missing_selected_title_marks_failed_after_max(self, session):
        """No selected title variant causes exception; handled by retry logic."""
        listing = _make_listing(session, status="approved")
        listing.push_attempts = 2
        session.commit()
        # Only add a mockup — no title variant
        _add_selected_mockup(session, listing.id)
        listing_id = listing.id

        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient"), \
             patch("app.workers.etsy_uploader.NotionClient"):

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result = run_etsy_uploader_job()

        assert result["status"] == "failed"
        session.expire_all()
        assert session.get(Listing, listing_id).status == "failed"


class TestEtsyUploaderAtomicClaim:
    def test_second_call_finds_no_listing_when_first_claimed(self, session):
        """Simulates concurrency: second call should find listing already pushed."""
        listing = _make_listing(session, status="approved")
        _add_selected_title(session, listing.id)
        _add_selected_mockup(session, listing.id)

        mock_etsy = MagicMock()
        mock_notion_instance = MagicMock()

        with patch("app.workers.etsy_uploader.SessionLocal", return_value=session), \
             patch("app.workers.etsy_uploader.EtsyApiClient") as MockEtsy, \
             patch("app.workers.etsy_uploader.NotionClient", return_value=mock_notion_instance):

            MockEtsy.return_value.__enter__ = lambda s: mock_etsy
            MockEtsy.return_value.__exit__ = MagicMock(return_value=False)

            from app.workers.etsy_uploader import run_etsy_uploader_job
            result1 = run_etsy_uploader_job()
            result2 = run_etsy_uploader_job()  # no approved listing remains

        assert result1["status"] == "pushed"
        assert result2["status"] == "idle"
        assert mock_etsy.update_listing.call_count == 1


# ---------------------------------------------------------------------------
# Tests: admin endpoint
# ---------------------------------------------------------------------------

class TestAdminEndpoint:
    @pytest.fixture()
    def client(self):
        from app.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_missing_token_returns_401(self, client):
        """POST /admin/run-uploader without token → 401."""
        with patch("app.routes.admin.settings") as mock_settings:
            mock_settings.admin_token = "secret-token"
            resp = client.post("/admin/run-uploader")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        """POST /admin/run-uploader with wrong token → 401."""
        with patch("app.routes.admin.settings") as mock_settings:
            mock_settings.admin_token = "secret-token"
            resp = client.post(
                "/admin/run-uploader",
                headers={"X-Admin-Token": "wrong-token"},
            )
        assert resp.status_code == 401

    def test_valid_token_returns_200(self, client):
        """POST /admin/run-uploader with correct token → 200 with result."""
        mock_result = {"listing_id": None, "status": "idle", "error": None}
        with patch("app.routes.admin.settings") as mock_settings, \
             patch("app.workers.etsy_uploader.run_etsy_uploader_job", return_value=mock_result):
            mock_settings.admin_token = "secret-token"
            resp = client.post(
                "/admin/run-uploader",
                headers={"X-Admin-Token": "secret-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "triggered"

    def test_unconfigured_admin_token_returns_503(self, client):
        """POST /admin/run-uploader when ADMIN_TOKEN not set → 503."""
        with patch("app.routes.admin.settings") as mock_settings:
            mock_settings.admin_token = ""
            resp = client.post(
                "/admin/run-uploader",
                headers={"X-Admin-Token": "anything"},
            )
        assert resp.status_code == 503
