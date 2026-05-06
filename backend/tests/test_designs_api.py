"""Tests for /designs JSON API — upload, list, delete with R2 mocked."""
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401 — register table
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-designs"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    from app import config
    config.settings.admin_token = ADMIN_TOKEN
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


def _make_png_rgba(width: int = 100, height: int = 100) -> bytes:
    """Return minimal valid RGBA PNG bytes."""
    img = Image.new("RGBA", (width, height), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpg() -> bytes:
    img = Image.new("RGB", (50, 50), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_r2():
    """Return a mock instance for R2StorageClient."""
    mock = MagicMock()
    mock.upload_image.return_value = "https://cdn.example.com/designs/abc.png"
    mock.delete_object.return_value = None
    mock.object_exists.return_value = False
    mock.list_objects.return_value = []
    return mock


def _patch_r2(mock_instance):
    """Patch R2StorageClient at the source module so lazy imports resolve to mock."""
    return patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_instance)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_upload_design_requires_token(client):
    resp = client.post("/designs", data={"name": "x"}, files={"file": ("x.png", b"", "image/png")})
    assert resp.status_code == 401


def test_list_designs_requires_token(client):
    resp = client.get("/designs")
    assert resp.status_code == 401


def test_delete_design_requires_token(client):
    resp = client.delete("/designs/1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Upload — success
# ---------------------------------------------------------------------------

def test_upload_png_with_alpha_success(client):
    png_bytes = _make_png_rgba()
    r2_mock = _mock_r2()
    with _patch_r2(r2_mock):
        resp = client.post(
            "/designs",
            headers=VALID_HEADERS,
            data={"name": "My Logo", "source_type": "upload"},
            files={"file": ("logo.png", png_bytes, "image/png")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "My Logo"
    assert body["source_type"] == "upload"
    assert body["width"] == 100
    assert body["height"] == 100
    assert "file_url" in body


# ---------------------------------------------------------------------------
# Upload — rejections
# ---------------------------------------------------------------------------

def test_upload_jpg_rejected(client):
    jpg_bytes = _make_jpg()
    resp = client.post(
        "/designs",
        headers=VALID_HEADERS,
        data={"name": "Bad", "source_type": "upload"},
        files={"file": ("img.jpg", jpg_bytes, "image/jpeg")},
    )
    assert resp.status_code == 400
    assert "PNG" in resp.json()["detail"]


def test_upload_oversized_rejected(client):
    # >10MB of random bytes with PNG header
    oversized = b"\x89PNG\r\n\x1a\n" + b"x" * (10 * 1024 * 1024 + 1)
    resp = client.post(
        "/designs",
        headers=VALID_HEADERS,
        data={"name": "Big", "source_type": "upload"},
        files={"file": ("big.png", oversized, "image/png")},
    )
    assert resp.status_code == 400
    assert "10 MB" in resp.json()["detail"]


def test_upload_png_no_alpha_rejected(client):
    # RGB PNG (no alpha)
    img = Image.new("RGB", (50, 50), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    resp = client.post(
        "/designs",
        headers=VALID_HEADERS,
        data={"name": "No alpha", "source_type": "upload"},
        files={"file": ("rgb.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 400
    assert "alpha" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_designs_empty(client):
    resp = client.get("/designs", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["designs"] == []


def test_list_designs_with_filter(client):
    png_bytes = _make_png_rgba()
    r2_mock = _mock_r2()
    with _patch_r2(r2_mock):
        client.post(
            "/designs", headers=VALID_HEADERS,
            data={"name": "Upload1", "source_type": "upload"},
            files={"file": ("a.png", png_bytes, "image/png")},
        )
        client.post(
            "/designs", headers=VALID_HEADERS,
            data={"name": "AI1", "source_type": "ai_generated"},
            files={"file": ("b.png", png_bytes, "image/png")},
        )

    resp = client.get("/designs?source_type=upload", headers=VALID_HEADERS)
    assert resp.status_code == 200
    designs = resp.json()["designs"]
    assert len(designs) == 1
    assert designs[0]["source_type"] == "upload"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_design_success(client):
    png_bytes = _make_png_rgba()
    r2_mock = _mock_r2()
    with _patch_r2(r2_mock):
        upload_resp = client.post(
            "/designs", headers=VALID_HEADERS,
            data={"name": "ToDelete", "source_type": "upload"},
            files={"file": ("d.png", png_bytes, "image/png")},
        )
    design_id = upload_resp.json()["id"]

    with _patch_r2(r2_mock):
        resp = client.delete(f"/designs/{design_id}", headers=VALID_HEADERS)
    assert resp.status_code == 204


def test_delete_nonexistent_design_404(client):
    r2_mock = _mock_r2()
    with _patch_r2(r2_mock):
        resp = client.delete("/designs/99999", headers=VALID_HEADERS)
    assert resp.status_code == 404
