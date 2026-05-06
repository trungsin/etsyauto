"""Tests for /references JSON API — scrape, get, list, update, delete."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-refs"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

_SCRAPE_PAYLOAD = {
    "source_url": "https://www.etsy.com/listing/12345/test-item",
    "source_listing_id": "12345",
    "original_title": "Test Listing Title",
    "original_description": "A lovely item.",
    "original_images": ["https://img.example.com/1.jpg", "https://img.example.com/2.jpg"],
}


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


# ---------------------------------------------------------------------------
# Auth guard — all 5 endpoints must return 401 without token
# ---------------------------------------------------------------------------

def test_scrape_requires_token(client):
    resp = client.post("/references/scrape", json=_SCRAPE_PAYLOAD)
    assert resp.status_code == 401


def test_list_requires_token(client):
    resp = client.get("/references")
    assert resp.status_code == 401


def test_get_requires_token(client):
    resp = client.get("/references/1")
    assert resp.status_code == 401


def test_update_requires_token(client):
    resp = client.put("/references/1", json={"notes": "test"})
    assert resp.status_code == 401


def test_delete_requires_token(client):
    resp = client.delete("/references/1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scrape — success (201)
# ---------------------------------------------------------------------------

def test_scrape_creates_reference(client):
    resp = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["source_listing_id"] == "12345"
    assert body["status"] == "scraped"
    assert body["original_images"] == _SCRAPE_PAYLOAD["original_images"]


# ---------------------------------------------------------------------------
# Scrape — idempotency (200 on duplicate listing_id)
# ---------------------------------------------------------------------------

def test_scrape_idempotent_same_listing_id(client):
    r1 = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    r2 = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == first_id


# ---------------------------------------------------------------------------
# Scrape — validation: >10 images → 400
# ---------------------------------------------------------------------------

def test_scrape_too_many_images_rejected(client):
    payload = {**_SCRAPE_PAYLOAD, "source_listing_id": "99999", "original_images": [f"https://img.example.com/{i}.jpg" for i in range(11)]}
    resp = client.post("/references/scrape", headers=VALID_HEADERS, json=payload)
    assert resp.status_code == 422  # Pydantic validator fires before service


# ---------------------------------------------------------------------------
# GET single — 200 / 404
# ---------------------------------------------------------------------------

def test_get_reference_success(client):
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref_id = r.json()["id"]
    resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["id"] == ref_id


def test_get_reference_not_found(client):
    resp = client.get("/references/99999", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List — empty + filter by tags + filter by status
# ---------------------------------------------------------------------------

def test_list_references_empty(client):
    resp = client.get("/references", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["references"] == []


def test_list_references_returns_created(client):
    client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    resp = client.get("/references", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["references"]) == 1


def test_list_filter_by_tags(client):
    # Create two references, tag one with 'style'
    r1 = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref1_id = r1.json()["id"]
    client.put(f"/references/{ref1_id}", headers=VALID_HEADERS, json={"tags": ["style"]})

    payload2 = {**_SCRAPE_PAYLOAD, "source_listing_id": "99001"}
    client.post("/references/scrape", headers=VALID_HEADERS, json=payload2)

    resp = client.get("/references?tags=style", headers=VALID_HEADERS)
    assert resp.status_code == 200
    refs = resp.json()["references"]
    assert len(refs) == 1
    assert refs[0]["id"] == ref1_id


def test_list_filter_by_status(client):
    client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    payload2 = {**_SCRAPE_PAYLOAD, "source_listing_id": "99002"}
    r2 = client.post("/references/scrape", headers=VALID_HEADERS, json=payload2)
    ref2_id = r2.json()["id"]
    client.put(f"/references/{ref2_id}", headers=VALID_HEADERS, json={"status": "enriched"})

    resp = client.get("/references?status=enriched", headers=VALID_HEADERS)
    refs = resp.json()["references"]
    assert len(refs) == 1
    assert refs[0]["id"] == ref2_id


# ---------------------------------------------------------------------------
# Update — partial fields
# ---------------------------------------------------------------------------

def test_update_partial_fields(client):
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref_id = r.json()["id"]

    resp = client.put(
        f"/references/{ref_id}",
        headers=VALID_HEADERS,
        json={"edited_title": "My Better Title", "notes": "Great colors", "tags": ["color", "style"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited_title"] == "My Better Title"
    assert body["notes"] == "Great colors"
    assert set(body["tags"]) == {"color", "style"}


def test_update_invalid_tag_rejected(client):
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref_id = r.json()["id"]
    resp = client.put(f"/references/{ref_id}", headers=VALID_HEADERS, json={"tags": ["invalid_tag"]})
    assert resp.status_code == 422


def test_update_not_found(client):
    resp = client.put("/references/99999", headers=VALID_HEADERS, json={"notes": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete — 204 success + cascades cutout_design_id to NULL
# ---------------------------------------------------------------------------

def test_delete_reference_success(client):
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref_id = r.json()["id"]

    resp = client.delete(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert resp.status_code == 204

    # Confirm gone
    assert client.get(f"/references/{ref_id}", headers=VALID_HEADERS).status_code == 404


def test_delete_cascades_cutout_design_ref(client):
    """When reference with cutout_design_id is deleted, design row is also cleaned up."""
    from unittest.mock import MagicMock, patch

    # Create reference
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    ref_id = r.json()["id"]

    # Manually set cutout_design_id via DB to a non-existent design (no R2 needed)
    db = _TestingSessionLocal()
    try:
        from app.models.reference import Reference as RefModel
        ref_row = db.get(RefModel, ref_id)
        # Insert a dummy Design first so FK constraint is satisfied
        from app.models.design import Design as DesignModel
        d = DesignModel(name="cutout", source_type="reference_only", file_url="https://r2.test/x.png", width=100, height=100)
        db.add(d)
        db.commit()
        db.refresh(d)
        design_id = d.id
        ref_row.cutout_design_id = design_id
        db.commit()
    finally:
        db.close()

    # Mock R2 delete so no real call
    with patch("app.clients.r2_storage_client.R2StorageClient") as mock_r2_cls:
        mock_r2_cls.return_value = MagicMock()
        resp = client.delete(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert resp.status_code == 204

    # Design row should also be gone
    db2 = _TestingSessionLocal()
    try:
        from app.models.design import Design as DesignModel
        assert db2.get(DesignModel, design_id) is None
    finally:
        db2.close()


def test_delete_not_found(client):
    resp = client.delete("/references/99999", headers=VALID_HEADERS)
    assert resp.status_code == 404
