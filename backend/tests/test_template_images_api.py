"""Tests for app.routes.template_images_admin — REST API endpoints."""
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.template_image import TemplateImage
from tests.conftest import seed_design, seed_template, seed_template_image


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-images"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client(monkeypatch):
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
# POST "" — upload image
# ---------------------------------------------------------------------------


def test_upload_creates_row_and_uploads_to_r2(client, db_session):
    """POST /admin/templates/{id}/images uploads file + creates DB row."""
    template = seed_template(db_session)

    mock_r2 = MagicMock()
    mock_r2.upload_image.return_value = "https://cdn.example.com/template-images/t1/abc.png"

    with patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_r2):
        file_data = io.BytesIO(b"PNG_DATA")
        resp = client.post(
            f"/admin/templates/{template.id}/images",
            headers=ADMIN_HEADERS,
            files={"file": ("photo.png", file_data, "image/png")},
            data={"color": "White", "rank": 1, "role": "mockup"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] is not None
    assert body["color"] == "White"
    assert body["rank"] == 1
    assert body["image_url"] == "https://cdn.example.com/template-images/t1/abc.png"


def test_upload_rejects_oversized(client):
    """POST rejects files > 20 MB."""
    # Patch to avoid DB access for this request
    with patch("app.clients.r2_storage_client.R2StorageClient"):
        big_data = io.BytesIO(b"x" * (21 * 1024 * 1024))
        resp = client.post(
            f"/admin/templates/1/images",
            headers=ADMIN_HEADERS,
            files={"file": ("big.png", big_data, "image/png")},
        )

    assert resp.status_code == 413


def test_upload_rejects_invalid_mime(client):
    """POST rejects non-image MIME types."""
    with patch("app.clients.r2_storage_client.R2StorageClient"):
        resp = client.post(
            f"/admin/templates/1/images",
            headers=ADMIN_HEADERS,
            files={"file": ("document.pdf", io.BytesIO(b"PDF_DATA"), "application/pdf")},
        )

    assert resp.status_code == 415


def test_all_endpoints_require_admin_token(client, db_session):
    """All endpoints return 401 without X-Admin-Token."""
    template = seed_template(db_session)
    img = seed_template_image(db_session, template.id, rank=0)

    # POST without token
    resp = client.post(
        f"/admin/templates/{template.id}/images",
        files={"file": ("photo.png", io.BytesIO(b"PNG"), "image/png")},
    )
    assert resp.status_code == 401

    # GET without token
    resp = client.get(f"/admin/templates/{template.id}/images")
    assert resp.status_code == 401

    # PATCH without token
    resp = client.patch(
        f"/admin/templates/{template.id}/images/{img.id}",
        json={"rank": 2},
    )
    assert resp.status_code == 401

    # DELETE without token
    resp = client.delete(f"/admin/templates/{template.id}/images/{img.id}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET "" — list images
# ---------------------------------------------------------------------------


def test_get_lists_template_images(client, db_session):
    """GET /admin/templates/{id}/images lists pool."""
    template = seed_template(db_session)
    img1 = seed_template_image(db_session, template.id, rank=0, color=None)
    img2 = seed_template_image(db_session, template.id, rank=1, color="White")

    resp = client.get(
        f"/admin/templates/{template.id}/images",
        headers=ADMIN_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["id"] == img1.id
    assert body[1]["color"] == "White"


# ---------------------------------------------------------------------------
# PATCH "/{img_id}" — update image
# ---------------------------------------------------------------------------


def test_patch_updates_anchor_invalidates_cache(client, db_session):
    """PATCH updates fields + invalidates composites."""
    template = seed_template(db_session)
    img = seed_template_image(db_session, template.id, rank=1, color="White")

    with patch("app.services.composite_service.invalidate_composites_for_image") as mock_inv:
        resp = client.patch(
            f"/admin/templates/{template.id}/images/{img.id}",
            headers=ADMIN_HEADERS,
            json={"rank": 2, "anchor_json": '{"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}'},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rank"] == 2
    mock_inv.assert_called_once_with(img.id)


def test_patch_returns_404_for_missing_image(client, db_session):
    """PATCH nonexistent image returns 404."""
    template = seed_template(db_session)

    resp = client.patch(
        f"/admin/templates/{template.id}/images/999",
        headers=ADMIN_HEADERS,
        json={"rank": 1},
    )

    assert resp.status_code == 404


def test_patch_returns_404_for_wrong_template(client, db_session):
    """PATCH with image from different template returns 404."""
    template1 = seed_template(db_session)
    template2 = seed_template(db_session, name="Other")
    img = seed_template_image(db_session, template2.id, rank=1)

    resp = client.patch(
        f"/admin/templates/{template1.id}/images/{img.id}",
        headers=ADMIN_HEADERS,
        json={"rank": 2},
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE "/{img_id}" — delete image
# ---------------------------------------------------------------------------


def test_delete_removes_row_and_r2_object(client, db_session):
    """DELETE removes TemplateImage + R2 object."""
    template = seed_template(db_session)
    img = seed_template_image(
        db_session,
        template.id,
        rank=1,
        image_url="https://cdn.example.com/template-images/t1/abc.png",
    )

    mock_r2 = MagicMock()

    with patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_r2):
        with patch("app.services.composite_service.invalidate_composites_for_image"):
            resp = client.delete(
                f"/admin/templates/{template.id}/images/{img.id}",
                headers=ADMIN_HEADERS,
            )

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Verify R2 delete was called
    mock_r2.delete_object.assert_called_once_with("template-images/t1/abc.png")

    # Verify delete happened (row should be gone from fresh query)
    from sqlalchemy import select
    from app.database import SessionLocal
    with SessionLocal() as fresh_session:
        deleted = fresh_session.scalars(
            select(TemplateImage).where(TemplateImage.id == img.id)
        ).first()
        assert deleted is None


def test_delete_returns_404_for_missing(client, db_session):
    """DELETE nonexistent image returns 404."""
    template = seed_template(db_session)

    resp = client.delete(
        f"/admin/templates/{template.id}/images/999",
        headers=ADMIN_HEADERS,
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST "/reorder" — bulk reorder
# ---------------------------------------------------------------------------


def test_reorder_swaps_ranks(client, db_session):
    """POST /reorder swaps ranks."""
    template = seed_template(db_session)
    img1 = seed_template_image(db_session, template.id, rank=1)
    img2 = seed_template_image(db_session, template.id, rank=2)

    resp = client.post(
        f"/admin/templates/{template.id}/images/reorder",
        headers=ADMIN_HEADERS,
        json=[
            {"id": img1.id, "rank": 2},
            {"id": img2.id, "rank": 1},
        ],
    )

    assert resp.status_code == 200
    assert resp.json()["reordered"] == 2

    db_session.refresh(img1)
    db_session.refresh(img2)
    assert img1.rank == 2
    assert img2.rank == 1


def test_reorder_rejects_foreign_id(client, db_session):
    """POST /reorder with image from different template fails."""
    template1 = seed_template(db_session)
    template2 = seed_template(db_session, name="Other")
    img1 = seed_template_image(db_session, template1.id, rank=1)
    img2 = seed_template_image(db_session, template2.id, rank=1)

    resp = client.post(
        f"/admin/templates/{template1.id}/images/reorder",
        headers=ADMIN_HEADERS,
        json=[
            {"id": img1.id, "rank": 1},
            {"id": img2.id, "rank": 2},
        ],
    )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST "/migrate" — materialize virtual rows
# ---------------------------------------------------------------------------


def test_migrate_idempotent(client, db_session):
    """POST /migrate materializes virtual rows, idempotent."""
    template = seed_template(db_session, colors=["White", "Black"])

    # First call
    resp = client.post(
        f"/admin/templates/{template.id}/images/migrate",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["materialized"] == 3  # base + 2 colors

    # Second call should return 0
    resp = client.post(
        f"/admin/templates/{template.id}/images/migrate",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["materialized"] == 0
