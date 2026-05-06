"""Tests for Phase 6: Notion review integration + R2 image upload.

All external APIs (notion-client SDK, boto3/R2) are mocked — no real calls made.
"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant

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

def _make_listing(session, status="review", notion_page_id=None) -> Listing:
    listing = Listing(
        etsy_listing_id="12345678",
        original_title="Handmade Ceramic Mug",
        original_desc="A beautiful mug",
        original_tags=json.dumps(["ceramic", "handmade"]),
        original_images=json.dumps(["https://example.com/img.jpg"]),
        status=status,
        notion_page_id=notion_page_id,
    )
    session.add(listing)
    session.commit()
    session.refresh(listing)
    return listing


def _add_title_variants(session, listing_id: int, count: int = 3) -> list[TitleVariant]:
    variants = []
    for i in range(count):
        v = TitleVariant(
            listing_id=listing_id,
            text=f"Amazing Ceramic Mug Variant {i + 1}",
            char_count=30 + i,
            model="claude-sonnet-4-6",
            prompt_version="v1",
            ai_rationale=f"Rationale {i + 1}",
            selected=False,
        )
        session.add(v)
        variants.append(v)
    session.commit()
    for v in variants:
        session.refresh(v)
    return variants


def _add_mockup_variants(session, listing_id: int, count: int = 3, with_url=True) -> list[MockupVariant]:
    variants = []
    for i in range(count):
        v = MockupVariant(
            listing_id=listing_id,
            source_image_url="https://example.com/img.jpg",
            removed_bg_path=f"/static/bg_{i}.png",
            final_image_path=f"/static/final_{i}.png",
            final_image_url=f"https://r2.example.com/mockups/final_{i}.png" if with_url else None,
            scene_prompt=f"Rustic kitchen scene {i + 1}",
            model="gemini-2.5-flash",
            selected=False,
        )
        session.add(v)
        variants.append(v)
    session.commit()
    for v in variants:
        session.refresh(v)
    return variants


# ---------------------------------------------------------------------------
# Test 1: R2 upload — happy path
# ---------------------------------------------------------------------------

class TestR2Upload:
    def test_upload_returns_public_url(self, tmp_path):
        """upload_to_r2 returns a public URL when R2 is properly configured."""
        test_file = tmp_path / "mockup.png"
        img = Image.new("RGBA", (64, 64), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        test_file.write_bytes(buf.getvalue())

        with patch("app.services.image_service.settings") as mock_settings, \
             patch("app.clients.r2_storage_client.settings") as mock_r2_settings, \
             patch("boto3.client") as mock_boto3:

            mock_settings.static_dir = str(tmp_path)
            mock_r2_settings.r2_endpoint = "https://fake.r2.cloudflarestorage.com"
            mock_r2_settings.r2_access_key_id = "test-key-id"
            mock_r2_settings.r2_secret_access_key = "test-secret"
            mock_r2_settings.r2_bucket_name = "etsyauto-mockups"
            mock_r2_settings.r2_public_url = "https://pub.r2.example.com"

            mock_s3 = MagicMock()
            mock_boto3.return_value = mock_s3

            from app.services.image_service import upload_to_r2
            result = upload_to_r2(str(test_file))

        assert result is not None
        assert result.startswith("https://pub.r2.example.com/mockups/")
        mock_s3.put_object.assert_called_once()

    def test_upload_returns_none_when_r2_not_configured(self, tmp_path):
        """upload_to_r2 returns None (non-fatal) when R2 credentials are missing."""
        test_file = tmp_path / "mockup.png"
        test_file.write_bytes(b"fake png bytes")

        with patch("app.clients.r2_storage_client.settings") as mock_r2_settings:
            mock_r2_settings.r2_endpoint = ""
            mock_r2_settings.r2_access_key_id = ""
            mock_r2_settings.r2_secret_access_key = ""
            mock_r2_settings.r2_bucket_name = ""
            mock_r2_settings.r2_public_url = ""

            from app.services.image_service import upload_to_r2
            result = upload_to_r2(str(test_file))

        assert result is None

    def test_upload_returns_none_for_missing_file(self):
        """upload_to_r2 returns None (non-fatal) when file does not exist."""
        from app.services.image_service import upload_to_r2
        result = upload_to_r2("/nonexistent/path/file.png")
        assert result is None


# ---------------------------------------------------------------------------
# Test 2: NotionClient.create_review_page
# ---------------------------------------------------------------------------

class TestNotionCreateReviewPage:
    def test_create_review_page_returns_page_id(self, session):
        """create_review_page calls SDK with correct args and returns page_id."""
        listing = _make_listing(session, status="review")
        title_vars = _add_title_variants(session, listing.id)
        mockup_vars = _add_mockup_variants(session, listing.id)

        mock_sdk = MagicMock()
        mock_sdk.pages.create.return_value = {"id": "page-abc-123"}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.clients.notion_client import NotionClient
            client = NotionClient()
            page_id = client.create_review_page(listing, title_vars, mockup_vars)

        assert page_id == "page-abc-123"
        mock_sdk.pages.create.assert_called_once()
        call_kwargs = mock_sdk.pages.create.call_args[1]
        assert call_kwargs["parent"] == {"database_id": "db-uuid-123"}
        # Status must be 'review'
        assert call_kwargs["properties"]["Status"]["select"]["name"] == "review"
        # Children must include title heading and mockup heading
        children_text = str(call_kwargs["children"])
        assert "Title Variants" in children_text
        assert "Mockup Variants" in children_text

    def test_create_review_page_embeds_r2_image_url(self, session):
        """Image blocks use final_image_url (R2 public URL) when available."""
        listing = _make_listing(session, status="review")
        title_vars = _add_title_variants(session, listing.id)
        mockup_vars = _add_mockup_variants(session, listing.id, with_url=True)

        mock_sdk = MagicMock()
        mock_sdk.pages.create.return_value = {"id": "page-xyz"}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.clients.notion_client import NotionClient
            client = NotionClient()
            client.create_review_page(listing, title_vars, mockup_vars)

        children = mock_sdk.pages.create.call_args[1]["children"]
        image_blocks = [b for b in children if b.get("type") == "image"]
        assert len(image_blocks) == 3
        for block in image_blocks:
            url = block["image"]["external"]["url"]
            assert url.startswith("https://r2.example.com/mockups/")


# ---------------------------------------------------------------------------
# Test 3: sync_to_notion — idempotent
# ---------------------------------------------------------------------------

class TestSyncToNotionIdempotent:
    def test_already_synced_listing_is_skipped(self, session):
        """Listings with notion_page_id already set are not re-synced."""
        _make_listing(session, status="review", notion_page_id="existing-page-id")

        mock_sdk = MagicMock()
        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import sync_to_notion
            sync_to_notion()

        mock_sdk.pages.create.assert_not_called()

    def test_sync_creates_page_and_stores_id(self, session):
        """sync_to_notion creates a page and saves notion_page_id on the listing."""
        listing = _make_listing(session, status="review", notion_page_id=None)
        _add_title_variants(session, listing.id)
        _add_mockup_variants(session, listing.id)
        listing_id = listing.id

        mock_sdk = MagicMock()
        mock_sdk.pages.create.return_value = {"id": "new-page-999"}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import sync_to_notion
            sync_to_notion()

        session.expire_all()
        updated = session.get(Listing, listing_id)
        assert updated.notion_page_id == "new-page-999"

    def test_sync_skips_non_review_listings(self, session):
        """sync_to_notion ignores listings not in 'review' status."""
        _make_listing(session, status="pending", notion_page_id=None)

        mock_sdk = MagicMock()
        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import sync_to_notion
            sync_to_notion()

        mock_sdk.pages.create.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: pull_approvals — state transition
# ---------------------------------------------------------------------------

class TestPullApprovalsStateTransition:
    def _make_approved_page(self, listing_id: int, title_sel="variant-1", mockup_sel="variant-2") -> dict:
        return {
            "id": "page-approved-001",
            "properties": {
                "Status": {"select": {"name": "Approved"}},
                "SQLite ID": {"number": listing_id},
                "Selected Title": {"select": {"name": title_sel}},
                "Selected Mockup": {"select": {"name": mockup_sel}},
            },
        }

    def test_approved_page_transitions_listing_to_approved(self, session):
        """pull_approvals sets listing.status='approved' and marks correct variants."""
        listing = _make_listing(session, status="review", notion_page_id="page-approved-001")
        _add_title_variants(session, listing.id, count=3)
        _add_mockup_variants(session, listing.id, count=3)
        listing_id = listing.id

        page = self._make_approved_page(listing_id, title_sel="variant-2", mockup_sel="variant-3")
        mock_sdk = MagicMock()
        mock_sdk.databases.query.return_value = {"results": [page], "has_more": False}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import pull_approvals
            pull_approvals()

        session.expire_all()
        listing = session.get(Listing, listing_id)
        assert listing.status == "approved"

        title_variants = (
            session.query(TitleVariant)
            .filter(TitleVariant.listing_id == listing_id)
            .order_by(TitleVariant.id)
            .all()
        )
        assert title_variants[1].selected is True   # variant-2 = index 1
        assert title_variants[0].selected is False
        assert title_variants[2].selected is False

        mockup_variants = (
            session.query(MockupVariant)
            .filter(MockupVariant.listing_id == listing_id)
            .order_by(MockupVariant.id)
            .all()
        )
        assert mockup_variants[2].selected is True  # variant-3 = index 2

    def test_already_approved_listing_is_skipped(self, session):
        """pull_approvals does not re-process listings already marked approved."""
        listing = _make_listing(session, status="approved", notion_page_id="page-001")
        _add_title_variants(session, listing.id)
        _add_mockup_variants(session, listing.id)
        listing_id = listing.id

        page = self._make_approved_page(listing_id)
        mock_sdk = MagicMock()
        mock_sdk.databases.query.return_value = {"results": [page], "has_more": False}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import pull_approvals
            pull_approvals()

        session.expire_all()
        listing = session.get(Listing, listing_id)
        assert listing.status == "approved"  # unchanged

    def test_missing_selection_skips_approval(self, session):
        """pull_approvals skips pages where Selected Title or Mockup is not set."""
        listing = _make_listing(session, status="review", notion_page_id="page-001")
        _add_title_variants(session, listing.id)
        _add_mockup_variants(session, listing.id)
        listing_id = listing.id

        page = {
            "id": "page-001",
            "properties": {
                "Status": {"select": {"name": "Approved"}},
                "SQLite ID": {"number": listing_id},
                "Selected Title": {"select": None},   # not set
                "Selected Mockup": {"select": {"name": "variant-1"}},
            },
        }
        mock_sdk = MagicMock()
        mock_sdk.databases.query.return_value = {"results": [page], "has_more": False}

        with patch("app.clients.notion_client.settings") as mock_settings, \
             patch("app.clients.notion_client.Client", return_value=mock_sdk), \
             patch("app.workers.notion_sync.SessionLocal", return_value=session):

            mock_settings.notion_api_key = "secret_test"
            mock_settings.notion_database_id = "db-uuid-123"

            from app.workers.notion_sync import pull_approvals
            pull_approvals()

        session.expire_all()
        assert session.get(Listing, listing_id).status == "review"  # unchanged


# ---------------------------------------------------------------------------
# Test 5: review_service validation
# ---------------------------------------------------------------------------

class TestReviewServiceValidation:
    def test_invalid_selection_key_raises(self, session):
        """mark_variants_selected raises ValueError for unknown variant keys."""
        listing = _make_listing(session, status="review")
        _add_title_variants(session, listing.id)
        _add_mockup_variants(session, listing.id)

        from app.services.review_service import mark_variants_selected
        with pytest.raises(ValueError, match="Unknown title selection"):
            mark_variants_selected(session, listing.id, "variant-99", "variant-1")

    def test_out_of_range_index_raises(self, session):
        """mark_variants_selected raises ValueError when index exceeds available variants."""
        listing = _make_listing(session, status="review")
        _add_title_variants(session, listing.id, count=1)   # only 1 title variant
        _add_mockup_variants(session, listing.id, count=3)

        from app.services.review_service import mark_variants_selected
        with pytest.raises(ValueError, match="title variant 2"):
            mark_variants_selected(session, listing.id, "variant-2", "variant-1")

    def test_valid_selection_marks_and_approves(self, session):
        """mark_variants_selected correctly sets selected flags and status."""
        listing = _make_listing(session, status="review")
        _add_title_variants(session, listing.id, count=3)
        _add_mockup_variants(session, listing.id, count=3)
        listing_id = listing.id

        from app.services.review_service import mark_variants_selected
        mark_variants_selected(session, listing_id, "variant-1", "variant-3")

        session.expire_all()
        assert session.get(Listing, listing_id).status == "approved"

        tvs = (
            session.query(TitleVariant)
            .filter(TitleVariant.listing_id == listing_id)
            .order_by(TitleVariant.id)
            .all()
        )
        assert tvs[0].selected is True
        assert tvs[1].selected is False
        assert tvs[2].selected is False

        mvs = (
            session.query(MockupVariant)
            .filter(MockupVariant.listing_id == listing_id)
            .order_by(MockupVariant.id)
            .all()
        )
        assert mvs[2].selected is True  # variant-3

    def test_notion_not_configured_sync_is_noop(self, session):
        """sync_to_notion is a no-op (logs warning) when Notion not configured."""
        _make_listing(session, status="review", notion_page_id=None)

        with patch("app.workers.notion_sync.SessionLocal", return_value=session), \
             patch("app.clients.notion_client.settings") as mock_settings:

            mock_settings.notion_api_key = ""
            mock_settings.notion_database_id = ""

            from app.workers.notion_sync import sync_to_notion
            # Should not raise
            sync_to_notion()
