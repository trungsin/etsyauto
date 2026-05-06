"""Tests for POST /references/{id}/save — Notion Idea Bank integration."""
import pytest
from unittest.mock import MagicMock, patch
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

ADMIN_TOKEN = "test-admin-token-notion-save"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

_SCRAPE_PAYLOAD = {
    "source_url": "https://www.etsy.com/listing/55555/notion-test-item",
    "source_listing_id": "55555",
    "original_title": "Notion Test Listing",
    "original_description": "Description for notion test.",
    "original_images": ["https://img.example.com/notion1.jpg"],
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


@pytest.fixture
def client_with_idea_bank(monkeypatch):
    """Client with NOTION_IDEA_BANK_DATA_SOURCE_ID configured."""
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("NOTION_IDEA_BANK_DATA_SOURCE_ID", "test-idea-bank-ds-id")
    from app import config
    config.settings.admin_token = ADMIN_TOKEN
    config.settings.notion_idea_bank_data_source_id = "test-idea-bank-ds-id"
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    config.settings.notion_idea_bank_data_source_id = ""


def _create_ref(client) -> int:
    """Helper to create a reference and return its id."""
    r = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_save_requires_token(client):
    r = client.post("/references/1/save")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 503 when NOTION_IDEA_BANK_DATA_SOURCE_ID not configured
# ---------------------------------------------------------------------------

def test_save_returns_503_when_idea_bank_not_configured(client):
    from app import config
    original = config.settings.notion_idea_bank_data_source_id
    config.settings.notion_idea_bank_data_source_id = ""
    try:
        ref_id = _create_ref(client)
        resp = client.post(f"/references/{ref_id}/save", headers=VALID_HEADERS)
        assert resp.status_code == 503
        assert "NOTION_IDEA_BANK_DATA_SOURCE_ID" in resp.json()["detail"]
    finally:
        config.settings.notion_idea_bank_data_source_id = original


# ---------------------------------------------------------------------------
# 404 when reference not found
# ---------------------------------------------------------------------------

def test_save_returns_404_for_missing_reference(client_with_idea_bank):
    mock_notion = MagicMock()
    mock_notion.create_idea_bank_page.return_value = "page-abc-123"
    with patch("app.clients.notion_client.NotionClient", return_value=mock_notion):
        resp = client_with_idea_bank.post("/references/99999/save", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Successful save — creates Notion page, stores page_id, status=saved
# ---------------------------------------------------------------------------

def test_save_creates_notion_page_and_updates_reference(client_with_idea_bank):
    ref_id = _create_ref(client_with_idea_bank)

    mock_notion = MagicMock()
    mock_notion.create_idea_bank_page.return_value = "page-new-001"

    with patch("app.clients.notion_client.NotionClient", return_value=mock_notion):
        resp = client_with_idea_bank.post(f"/references/{ref_id}/save", headers=VALID_HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["notion_page_id"] == "page-new-001"
    assert body["status"] == "saved"

    # Verify reference was updated in DB
    get_resp = client_with_idea_bank.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert get_resp.status_code == 200
    ref_data = get_resp.json()
    assert ref_data["notion_page_id"] == "page-new-001"
    assert ref_data["status"] == "saved"


# ---------------------------------------------------------------------------
# Idempotent save — second call updates existing page, page_id unchanged
# ---------------------------------------------------------------------------

def test_save_twice_updates_existing_page_not_duplicate(client_with_idea_bank):
    ref_id = _create_ref(client_with_idea_bank)

    mock_notion = MagicMock()
    mock_notion.create_idea_bank_page.return_value = "page-idempotent-001"
    mock_notion.update_idea_bank_page.return_value = None

    with patch("app.clients.notion_client.NotionClient", return_value=mock_notion):
        resp1 = client_with_idea_bank.post(f"/references/{ref_id}/save", headers=VALID_HEADERS)
        resp2 = client_with_idea_bank.post(f"/references/{ref_id}/save", headers=VALID_HEADERS)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Both return same page_id
    assert resp1.json()["notion_page_id"] == "page-idempotent-001"
    assert resp2.json()["notion_page_id"] == "page-idempotent-001"

    # create called once, update called once (second save)
    assert mock_notion.create_idea_bank_page.call_count == 1
    assert mock_notion.update_idea_bank_page.call_count == 1


# ---------------------------------------------------------------------------
# Save without cutout — page created, no cutout_url passed
# ---------------------------------------------------------------------------

def test_save_without_cutout_creates_page(client_with_idea_bank):
    ref_id = _create_ref(client_with_idea_bank)

    captured_cutout: list = []

    def _fake_create(reference, cutout_url=None):
        captured_cutout.append(cutout_url)
        return "page-no-cutout"

    mock_notion = MagicMock()
    mock_notion.create_idea_bank_page.side_effect = _fake_create

    with patch("app.clients.notion_client.NotionClient", return_value=mock_notion):
        resp = client_with_idea_bank.post(f"/references/{ref_id}/save", headers=VALID_HEADERS)

    assert resp.status_code == 200
    assert captured_cutout[0] is None  # no cutout


# ---------------------------------------------------------------------------
# DELETE reference with notion_page_id → archive_page called
# ---------------------------------------------------------------------------

def test_delete_reference_with_notion_page_id_archives_notion(client_with_idea_bank):
    ref_id = _create_ref(client_with_idea_bank)

    # Manually set notion_page_id in DB
    db = _TestingSessionLocal()
    try:
        ref_row = db.get(Reference, ref_id)
        ref_row.notion_page_id = "page-to-archive"
        db.commit()
    finally:
        db.close()

    mock_notion = MagicMock()
    mock_notion.archive_page.return_value = None

    with patch("app.clients.notion_client.NotionClient", return_value=mock_notion):
        resp = client_with_idea_bank.delete(f"/references/{ref_id}", headers=VALID_HEADERS)

    assert resp.status_code == 204
    mock_notion.archive_page.assert_called_once_with("page-to-archive")
