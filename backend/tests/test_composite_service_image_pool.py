"""Tests for app.services.composite_service with template_image_id pathway."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import composite_service
from tests.conftest import seed_design, seed_template, seed_template_image


class TestCacheKeyV3:
    """Test _cache_key_v3 — image_id-based cache keys."""

    def test_cache_key_uses_image_id_single_zone(self):
        """Single-zone cache key: composites/{tid}-{img_id}-{did}.png."""
        key = composite_service._cache_key_v3(
            template_id=10,
            image_id=5,
            design_id=3,
            zone_designs=None,
        )
        assert key == "composites/10-5-3.png"

    def test_cache_key_uses_image_id_multi_zone(self):
        """Multi-zone cache key includes hash."""
        key = composite_service._cache_key_v3(
            template_id=10,
            image_id=5,
            design_id=3,
            zone_designs={"main": 3, "detail": 7},
        )
        assert key.startswith("composites/10-5-")
        assert key.endswith("-multi.png")

    def test_cache_key_virtual_image_id(self):
        """Works with pseudo-ids like 'v0' for virtual rows."""
        key = composite_service._cache_key_v3(
            template_id=10,
            image_id="v0",
            design_id=3,
            zone_designs=None,
        )
        assert key == "composites/10-v0-3.png"


class TestGetOrCreateComposite:
    """Test get_or_create_composite with template_image_id."""

    def test_get_or_create_composite_with_image_id(self, db_session, monkeypatch):
        """get_or_create_composite with template_image_id uses new cache key."""
        template = seed_template(db_session)
        design = seed_design(db_session)
        img = seed_template_image(db_session, template.id, rank=0, color=None)

        # Mock R2 + image fetch
        mock_r2 = MagicMock()
        mock_r2.object_exists.return_value = False
        mock_r2.upload_image.return_value = "https://cdn.example.com/composites/c1.png"
        mock_r2.get_public_url.return_value = "https://cdn.example.com/composites/c1.png"

        def mock_fetch(url):
            if "templates" in url:
                return b"PNG_BASE"
            return b"PNG_DESIGN"

        with patch("app.services.composite_service._fetch_bytes", side_effect=mock_fetch):
            with patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_r2):
                with patch("app.services.image_composite.composite_zones", return_value=b"OUTPUT"):
                    url, cached = composite_service.get_or_create_composite(
                        db_session,
                        template_id=template.id,
                        design_id=design.id,
                        template_image_id=img.id,
                    )

        assert url == "https://cdn.example.com/composites/c1.png"
        assert cached is False
        # Verify new cache key format was used: composites/{tid}-{img_id}-{did}.png
        called_key = mock_r2.upload_image.call_args[0][1]
        assert called_key == f"composites/{template.id}-{img.id}-{design.id}.png"

    def test_get_or_create_composite_lifestyle_no_fill_short_circuit(
        self, db_session, monkeypatch
    ):
        """lifestyle_no_fill role skips rendering, returns image_url directly."""
        template = seed_template(db_session)
        design = seed_design(db_session)
        img = seed_template_image(
            db_session,
            template.id,
            rank=0,
            color=None,
            role="lifestyle_no_fill",
            image_url="https://cdn.example.com/lifestyle.png",
        )

        url, cached = composite_service.get_or_create_composite(
            db_session,
            template_id=template.id,
            design_id=design.id,
            template_image_id=img.id,
        )

        assert url == "https://cdn.example.com/lifestyle.png"
        assert cached is True

    def test_get_or_create_composite_image_id_not_in_template(self, db_session):
        """image_id from different template is rejected."""
        template1 = seed_template(db_session)
        template2 = seed_template(db_session, name="Other")
        design = seed_design(db_session)
        img = seed_template_image(db_session, template2.id, rank=0)

        with pytest.raises(ValueError, match="not in template"):
            composite_service.get_or_create_composite(
                db_session,
                template_id=template1.id,
                design_id=design.id,
                template_image_id=img.id,
            )


class TestRenderAllForListing:
    """Test render_all_for_listing — parallel rendering."""

    def test_render_all_for_listing_parallel(self, db_session, monkeypatch):
        """render_all_for_listing renders all images in parallel."""
        template = seed_template(db_session, colors=["White", "Black"])
        design = seed_design(db_session)

        # Seed 3 template images: universal + 2 colors
        img_universal = seed_template_image(db_session, template.id, rank=0, color=None)
        img_white = seed_template_image(db_session, template.id, rank=1, color="White")
        img_black = seed_template_image(db_session, template.id, rank=2, color="Black")

        # Mock R2 + fetch
        mock_r2 = MagicMock()
        mock_r2.object_exists.return_value = False
        mock_r2.upload_image.return_value = "https://cdn.example.com/composites/c.png"

        def mock_fetch(url):
            return b"PNG_DATA"

        with patch("app.services.composite_service._fetch_bytes", side_effect=mock_fetch):
            with patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_r2):
                with patch(
                    "app.services.image_composite.composite_zones", return_value=b"OUTPUT"
                ):
                    results = composite_service.render_all_for_listing(
                        db_session,
                        template_id=template.id,
                        design_id=design.id,
                    )

        # Should have 3 results
        assert len(results) == 3
        assert results[0]["id"] == img_universal.id
        assert results[1]["id"] == img_white.id
        assert results[2]["id"] == img_black.id

        # All should have URLs (no errors)
        assert all(r["url"] is not None for r in results)
        assert all(r["error"] is None for r in results)

    def test_render_all_handles_per_image_errors(self, db_session, monkeypatch):
        """Errors on one image don't halt the rest."""
        template = seed_template(db_session, colors=["White", "Black"])
        design = seed_design(db_session)

        img_universal = seed_template_image(db_session, template.id, rank=0, color=None)
        img_white = seed_template_image(
            db_session,
            template.id,
            rank=1,
            color="White",
            image_url="https://cdn.example.com/templates/white_color.png",
        )
        img_black = seed_template_image(db_session, template.id, rank=2, color="Black")

        # Mock R2
        mock_r2 = MagicMock()
        mock_r2.object_exists.return_value = False

        def mock_fetch_with_error(url):
            if "white_color" in url.lower():
                raise ValueError("Download failed")
            return b"PNG_DATA"

        with patch(
            "app.services.composite_service._fetch_bytes", side_effect=mock_fetch_with_error
        ):
            with patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_r2):
                with patch(
                    "app.services.image_composite.composite_zones", return_value=b"OUTPUT"
                ):
                    results = composite_service.render_all_for_listing(
                        db_session,
                        template_id=template.id,
                        design_id=design.id,
                    )

        # All 3 returned
        assert len(results) == 3

        # White one errored
        white_result = next((r for r in results if r["id"] == img_white.id), None)
        assert white_result is not None
        assert white_result["error"] is not None
        assert "Download failed" in white_result["error"]

        # Others succeeded
        assert results[0]["error"] is None
        assert results[2]["error"] is None


class TestInvalidateForImage:
    """Test invalidate_composites_for_image.

    Note: This function creates its own session internally, so full integration
    testing is better done via API layer (test_template_images_api.py).
    """

    def test_invalidate_function_signature(self):
        """invalidate_composites_for_image has correct signature."""
        # Just verify the function exists and is callable
        assert callable(composite_service.invalidate_composites_for_image)
