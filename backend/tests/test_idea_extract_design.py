"""Tests for POST /admin/ideas/{idea_id}/extract-design (phase 1 of v0.8.3).

All external I/O (httpx download, RemoveBgClient, R2StorageClient) is mocked —
no real network calls are made.
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design
from app.models.idea import Idea  # noqa: F401  — registers table on Base.metadata

# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-extract-design-token"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Drop + recreate all tables before each test for isolation."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client(monkeypatch):
    """TestClient with admin token configured and DB overridden."""
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    from app import config
    config.settings.admin_token = ADMIN_TOKEN
    config.settings.r2_public_url = "https://cdn.example.com"
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    s = _TestingSessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal RGBA PNG in memory."""
    img = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _seed_idea(
    session,
    *,
    reference_image_url: str | None = "https://i.etsystatic.com/img.jpg",
    status: str = "new",
) -> Idea:
    idea = Idea(
        source="etsy_api",
        source_listing_id="seed-001",
        reference_image_url=reference_image_url,
        status=status,
    )
    session.add(idea)
    session.commit()
    session.refresh(idea)
    return idea


def _seed_design(session, *, source_type: str = "derivative", file_url: str = "https://cdn.example.com/designs/derivative/1-000.png") -> Design:
    design = Design(
        name="old derivative",
        source_type=source_type,
        file_url=file_url,
        width=100,
        height=100,
    )
    session.add(design)
    session.commit()
    session.refresh(design)
    return design


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExtractDesignHappy:
    """POST /admin/ideas/{id}/extract-design — happy path."""

    def test_extract_design_happy(self, client, db_session):
        """200 response: design_id returned, idea.design_id set in DB."""
        idea = _seed_idea(db_session)
        png_bytes = _make_png_bytes()

        with (
            patch("app.routes.idea_wizard._download_with_retry", return_value=png_bytes),
            patch("app.routes.idea_wizard.RemoveBgClient") as mock_rbg_cls,
            patch("app.routes.idea_wizard.R2StorageClient") as mock_r2_cls,
        ):
            mock_rbg = MagicMock()
            mock_rbg.remove_bg.return_value = png_bytes  # already RGBA PNG
            mock_rbg_cls.return_value = mock_rbg

            mock_r2 = MagicMock()
            mock_r2.upload_image.return_value = "https://cdn.example.com/designs/derivative/1-123.png"
            mock_r2_cls.return_value = mock_r2

            resp = client.post(
                f"/admin/ideas/{idea.id}/extract-design",
                json={"crop_box": [0.0, 0.0, 0.5, 0.5]},
                headers=VALID_HEADERS,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "design_id" in data
        assert "file_url" in data
        assert data["design_id"] > 0

        # Verify DB state
        db_session.expire_all()
        updated_idea = db_session.get(Idea, idea.id)
        assert updated_idea.design_id == data["design_id"]

        design = db_session.get(Design, data["design_id"])
        assert design is not None
        assert design.source_type == "derivative"
        assert design.name == f"Derivative from idea #{idea.id}"


class TestExtractDesignErrorCases:
    """POST /admin/ideas/{id}/extract-design — error paths."""

    def test_extract_design_idea_not_found(self, client):
        """404 when idea does not exist."""
        resp = client.post(
            "/admin/ideas/99999/extract-design",
            json={"crop_box": [0.0, 0.0, 0.5, 0.5]},
            headers=VALID_HEADERS,
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_extract_design_no_reference_image(self, client, db_session):
        """400 when idea exists but reference_image_url is None."""
        idea = _seed_idea(db_session, reference_image_url=None)
        resp = client.post(
            f"/admin/ideas/{idea.id}/extract-design",
            json={"crop_box": [0.0, 0.0, 0.5, 0.5]},
            headers=VALID_HEADERS,
        )
        assert resp.status_code == 400
        assert "reference_image_url" in resp.json()["detail"]

    @pytest.mark.parametrize(
        "crop_box, description",
        [
            # Wrong length
            ([0.1, 0.1, 0.5], "too short — 3 elements"),
            ([0.1, 0.1, 0.5, 0.5, 0.0], "too long — 5 elements"),
            # Out of [0, 1]
            ([-0.1, 0.0, 0.5, 0.5], "x negative"),
            ([0.0, 1.1, 0.5, 0.5], "y > 1"),
            ([0.0, 0.0, 1.5, 0.5], "w > 1"),
            ([0.0, 0.0, 0.5, -0.1], "h negative"),
            # Zero area
            ([0.0, 0.0, 0.0, 0.5], "zero width"),
            ([0.0, 0.0, 0.5, 0.0], "zero height"),
            # Extends beyond bounds
            ([0.8, 0.0, 0.5, 0.5], "x+w > 1"),
            ([0.0, 0.8, 0.5, 0.5], "y+h > 1"),
        ],
    )
    def test_extract_design_invalid_crop_box(self, client, db_session, crop_box, description):
        """422 for various invalid crop_box values."""
        idea = _seed_idea(db_session)
        resp = client.post(
            f"/admin/ideas/{idea.id}/extract-design",
            json={"crop_box": crop_box},
            headers=VALID_HEADERS,
        )
        assert resp.status_code == 422, f"Expected 422 for: {description}; got {resp.status_code}: {resp.text}"


class TestExtractDesignOverwrite:
    """Idempotent overwrite: second call reuses same Design row, deletes old R2 key."""

    def test_extract_design_overwrite_existing(self, client, db_session):
        """Second extract on same idea: same design.id reused, R2 delete called once per overwrite."""
        idea = _seed_idea(db_session)
        png_bytes = _make_png_bytes()

        with (
            patch("app.routes.idea_wizard._download_with_retry", return_value=png_bytes),
            patch("app.routes.idea_wizard.RemoveBgClient") as mock_rbg_cls,
            patch("app.routes.idea_wizard.R2StorageClient") as mock_r2_cls,
        ):
            mock_rbg = MagicMock()
            mock_rbg.remove_bg.return_value = png_bytes
            mock_rbg_cls.return_value = mock_rbg

            mock_r2 = MagicMock()
            call_count = [0]

            def _upload_side_effect(file_bytes, key):
                call_count[0] += 1
                return f"https://cdn.example.com/{key}"

            mock_r2.upload_image.side_effect = _upload_side_effect
            mock_r2_cls.return_value = mock_r2

            # First call — creates new Design
            resp1 = client.post(
                f"/admin/ideas/{idea.id}/extract-design",
                json={"crop_box": [0.0, 0.0, 0.5, 0.5]},
                headers=VALID_HEADERS,
            )
            assert resp1.status_code == 200, resp1.text
            design_id_first = resp1.json()["design_id"]

            # Second call — should reuse same Design row
            resp2 = client.post(
                f"/admin/ideas/{idea.id}/extract-design",
                json={"crop_box": [0.1, 0.1, 0.4, 0.4]},
                headers=VALID_HEADERS,
            )
            assert resp2.status_code == 200, resp2.text
            design_id_second = resp2.json()["design_id"]

        # Same Design row reused
        assert design_id_first == design_id_second, (
            f"Expected same design id; got {design_id_first} vs {design_id_second}"
        )

        # R2 delete was called once (for the overwrite on second call)
        assert mock_r2.delete_object.call_count == 1

        # DB still has exactly one derivative design
        db_session.expire_all()
        updated_idea = db_session.get(Idea, idea.id)
        assert updated_idea.design_id == design_id_first
