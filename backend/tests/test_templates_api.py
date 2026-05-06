"""Tests for /templates JSON API and /admin/templates HTML endpoints.

Uses FastAPI TestClient with in-memory SQLite. R2StorageClient is mocked so
no real network calls are made.
"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401 — registers table on Base.metadata
from app.models.template import Template  # noqa: F401 — registers table on Base.metadata
from app.models.template_variation import TemplateVariation  # noqa: F401 — registers table

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# StaticPool forces all connections to share the same in-memory SQLite instance,
# preventing the "no such table" error that occurs when each connection gets its
# own empty in-memory DB.
_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-abc123"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Isolate each test with a fresh schema — drop/create every time."""
    # Import all models so their tables are registered on Base.metadata
    from app.models.template import Template  # noqa: F401
    from app.models.template_variation import TemplateVariation  # noqa: F401
    from app.models.design import Design  # noqa: F401
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    # Reload settings so the env var is picked up
    from app import config
    config.settings.admin_token = ADMIN_TOKEN

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


def _fake_r2_upload(self, file_bytes, key):  # noqa: ANN001
    return f"https://cdn.example.com/{key}"


def _fake_r2_delete(self, key):  # noqa: ANN001
    pass


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------

def test_list_templates_requires_token(client):
    resp = client.get("/templates")
    assert resp.status_code == 401


def test_create_template_requires_token(client):
    resp = client.post("/templates", data={"name": "x", "category": "apparel",
                                           "composite_anchor": '{"x":0.1,"y":0.1,"w":0.5,"h":0.5}'},
                       files={"base_image": ("img.png", b"fake", "image/png")})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Create template
# ---------------------------------------------------------------------------

def test_create_template_success(client):
    with patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "upload_image", _fake_r2_upload,
    ), patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "__init__", lambda self: None,
    ):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Cotton T-Shirt",
                "category": "apparel",
                "composite_anchor": '{"x":0.3,"y":0.25,"w":0.4,"h":0.5}',
                "default_price_cents": "2500",
                "variation_options": '{"sizes":["S","M","L"],"colors":["white","black"]}',
            },
            files={"base_image": ("tshirt.png", b"\x89PNG\r\n", "image/png")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Cotton T-Shirt"
    assert body["category"] == "apparel"
    assert body["default_price_cents"] == 2500
    assert body["composite_anchor"]["x"] == pytest.approx(0.3)
    assert body["variation_options"]["sizes"] == ["S", "M", "L"]
    assert "base_image_url" in body


def test_create_template_invalid_anchor(client):
    """Anchor values outside 0-1 must return 422."""
    with patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "__init__", lambda self: None,
    ):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Bad Anchor",
                "category": "apparel",
                "composite_anchor": '{"x":1.5,"y":0.25,"w":0.4,"h":0.5}',
                "default_price_cents": "0",
            },
            files={"base_image": ("img.png", b"fake", "image/png")},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List templates
# ---------------------------------------------------------------------------

def test_list_templates_empty(client):
    resp = client.get("/templates", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_templates_returns_created(client):
    with patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "upload_image", _fake_r2_upload,
    ), patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "__init__", lambda self: None,
    ):
        client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Mug",
                "category": "drinkware",
                "composite_anchor": '{"x":0.1,"y":0.1,"w":0.8,"h":0.8}',
                "default_price_cents": "1500",
            },
            files={"base_image": ("mug.png", b"fake", "image/png")},
        )

    resp = client.get("/templates", headers=VALID_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Mug"


# ---------------------------------------------------------------------------
# Delete cascades variations
# ---------------------------------------------------------------------------

def test_delete_template_not_found(client):
    resp = client.delete("/templates/9999", headers=VALID_HEADERS)
    assert resp.status_code == 404


def test_delete_template_success(client):
    # First create
    with patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "upload_image", _fake_r2_upload,
    ), patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "__init__", lambda self: None,
    ):
        create_resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "To Delete",
                "category": "print",
                "composite_anchor": '{"x":0.1,"y":0.1,"w":0.5,"h":0.5}',
                "default_price_cents": "0",
            },
            files={"base_image": ("img.png", b"fake", "image/png")},
        )
    template_id = create_resp.json()["id"]

    # Delete — mock R2 delete too
    with patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "delete_object", _fake_r2_delete,
    ), patch.object(
        __import__("app.clients.r2_storage_client", fromlist=["R2StorageClient"]).R2StorageClient,
        "__init__", lambda self: None,
    ):
        del_resp = client.delete(f"/templates/{template_id}", headers=VALID_HEADERS)
    assert del_resp.status_code == 204

    # Confirm gone
    get_resp = client.get(f"/templates/{template_id}", headers=VALID_HEADERS)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin UI list endpoint
# ---------------------------------------------------------------------------

def test_admin_templates_list_requires_token(client):
    resp = client.get("/admin/templates")
    assert resp.status_code == 401


def test_admin_templates_list_ok(client):
    resp = client.get("/admin/templates", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert b"Templates" in resp.content
