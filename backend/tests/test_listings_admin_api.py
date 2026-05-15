"""Tests for /admin/listings CRUD + action routes (v0.10)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design
from app.models.listing import Listing
from app.models.template import Template


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "admin-listings-test-token"
HDRS = {"X-Admin-Token": ADMIN_TOKEN}


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
def db():
    s = _TestingSessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_template(db) -> Template:
    t = Template(
        name="Admin Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/base.png",
        default_price_cents=1900,
        variation_options_json=json.dumps({
            "sizes": [{"name": "S", "price_cents": 1900}],
            "colors": ["White", "Black"],
            "primary_color": "White",
            "etsy_taxonomy_id": 1209,
        }),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _seed_design(db) -> Design:
    d = Design(
        name="logo", source_type="upload",
        file_url="https://cdn.example.com/d.png",
        width=1000, height=1000,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _seed_listing(db, *, status="new", etsy_id=None, tmpl=None, design=None) -> Listing:
    tmpl = tmpl or _seed_template(db)
    design = design or _seed_design(db)
    payload = {
        "title": "Hello", "description": "desc", "tags": ["t1"],
        "enabled_combos": [{"size": "S", "color": "White"}],
        "zone_designs": {},
        "gallery_snapshot": [{"rank": 1, "color": "White", "url": "https://cdn.example.com/c.png", "id": 1}],
    }
    row = Listing(
        etsy_listing_id=etsy_id,
        original_title="Hello",
        original_desc="desc",
        original_tags=json.dumps(["t1"]),
        original_images=json.dumps(["https://cdn.example.com/c.png"]),
        status=status,
        template_id=tmpl.id,
        design_id=design.id,
        local_payload_json=json.dumps(payload),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# GET /admin/listings — list filtering
# ---------------------------------------------------------------------------


def test_get_list_filters_by_status(client, db):
    tmpl = _seed_template(db)
    design = _seed_design(db)
    _seed_listing(db, status="new", tmpl=tmpl, design=design)
    _seed_listing(db, status="created", etsy_id="111", tmpl=tmpl, design=design)
    _seed_listing(db, status="failed", tmpl=tmpl, design=design)

    resp = client.get("/admin/listings?status=new", headers=HDRS)
    assert resp.status_code == 200
    body = resp.text
    # 1 row rendered for the 'new' filter; the badge class encodes the status.
    assert "badge-new" in body
    assert "badge-created" not in body
    assert "badge-failed" not in body
    assert "showing 1" in body


# ---------------------------------------------------------------------------
# GET /admin/listings/{id} — detail page renders composite urls
# ---------------------------------------------------------------------------


def test_get_detail_renders_composite_urls(client, db):
    row = _seed_listing(db, status="new")
    resp = client.get(f"/admin/listings/{row.id}", headers=HDRS)
    assert resp.status_code == 200
    # Composite URL from gallery_snapshot should appear in the rendered detail page
    assert "https://cdn.example.com/c.png" in resp.text


# ---------------------------------------------------------------------------
# PUT /admin/listings/{id} — edit
# ---------------------------------------------------------------------------


def test_put_edit_draft_updates_local_only(client, db):
    """PUT on a draft (status='new') updates local_payload + DB columns,
    does NOT call Etsy update_listing."""
    row = _seed_listing(db, status="new")

    with patch("app.routes.listings_admin.EtsyApiClient") as MockClient:
        resp = client.put(
            f"/admin/listings/{row.id}",
            headers=HDRS,
            json={"title": "New Title", "description": "New desc"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["etsy_synced"] is False
    MockClient.assert_not_called()

    db.refresh(row)
    assert row.original_title == "New Title"
    payload = json.loads(row.local_payload_json)
    assert payload["title"] == "New Title"
    assert payload["description"] == "New desc"


def test_put_edit_live_writes_through_to_etsy(client, db):
    """PUT on a live listing (status='created') calls Etsy update_listing for
    text fields."""
    row = _seed_listing(db, status="created", etsy_id="55555")

    etsy_mock = MagicMock()
    etsy_mock.__enter__ = MagicMock(return_value=etsy_mock)
    etsy_mock.__exit__ = MagicMock(return_value=False)
    etsy_mock.update_listing.return_value = {"listing_id": "55555"}

    with patch("app.routes.listings_admin.EtsyApiClient", return_value=etsy_mock):
        resp = client.put(
            f"/admin/listings/{row.id}",
            headers=HDRS,
            json={"title": "Updated Live Title"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["etsy_synced"] is True
    etsy_mock.update_listing.assert_called_once()
    call_kwargs = etsy_mock.update_listing.call_args
    assert call_kwargs.args[0] == "55555"
    assert call_kwargs.kwargs.get("title") == "Updated Live Title"


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/upload — push draft to Etsy
# ---------------------------------------------------------------------------


def test_post_upload_invokes_upload_to_etsy(client, db):
    row = _seed_listing(db, status="new")

    fake_result = {
        "listing_id": row.id,
        "etsy_listing_id": "77777",
        "draft_url": "https://www.etsy.com/your/shops/me/tools/listings/77777",
        "composite_urls": [],
        "idempotent": False,
    }

    with patch(
        "app.routes.listings_admin.listing_creator_service.upload_to_etsy",
        return_value=fake_result,
    ) as mock_upload, patch(
        "app.routes.listings_admin._resolve_shop_id", return_value=99,
    ):
        resp = client.post(f"/admin/listings/{row.id}/upload", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["etsy_listing_id"] == "77777"
    mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/sync — pull Etsy state
# ---------------------------------------------------------------------------


def test_post_sync_overwrites_local_fields(client, db):
    row = _seed_listing(db, status="created", etsy_id="55555")

    etsy_mock = MagicMock()
    etsy_mock.__enter__ = MagicMock(return_value=etsy_mock)
    etsy_mock.__exit__ = MagicMock(return_value=False)
    etsy_mock.get_listing.return_value = {
        "title": "Title From Etsy",
        "description": "Desc From Etsy",
        "tags": ["from", "etsy"],
        "state": "active",
    }

    with patch("app.routes.listings_admin.EtsyApiClient", return_value=etsy_mock):
        resp = client.post(f"/admin/listings/{row.id}/sync", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "created"
    assert body["title"] == "Title From Etsy"

    db.refresh(row)
    assert row.original_title == "Title From Etsy"
    assert row.original_desc == "Desc From Etsy"
    assert json.loads(row.original_tags) == ["from", "etsy"]


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/rerender — invalidate + re-render composites
# ---------------------------------------------------------------------------


def test_post_rerender_invalidates_cache_and_rerenders(client, db):
    row = _seed_listing(db, status="new")

    fake_rendered = [
        {"id": 1, "rank": 1, "color": "White",
         "url": "https://cdn.example.com/new-c.png", "image_url": "", "role": "mockup"},
    ]

    with patch(
        "app.routes.listings_admin.composite_service.invalidate_composites_for_template",
        return_value=3,
    ) as mock_inv, patch(
        "app.routes.listings_admin.composite_service.render_all_for_listing",
        return_value=fake_rendered,
    ) as mock_render:
        resp = client.post(f"/admin/listings/{row.id}/rerender", headers=HDRS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["gallery_count"] == 1
    mock_inv.assert_called_once_with(row.template_id)
    mock_render.assert_called_once()

    db.refresh(row)
    payload = json.loads(row.local_payload_json)
    assert payload["gallery_snapshot"][0]["url"] == "https://cdn.example.com/new-c.png"


# ---------------------------------------------------------------------------
# DELETE /admin/listings/{id} — Etsy DELETE + soft-delete local
# ---------------------------------------------------------------------------


def test_delete_calls_etsy_and_soft_deletes_local(client, db):
    row = _seed_listing(db, status="created", etsy_id="55555")

    etsy_mock = MagicMock()
    etsy_mock.__enter__ = MagicMock(return_value=etsy_mock)
    etsy_mock.__exit__ = MagicMock(return_value=False)
    etsy_mock.delete_listing.return_value = None

    with patch("app.routes.listings_admin.EtsyApiClient", return_value=etsy_mock), \
         patch("app.routes.listings_admin._resolve_shop_id", return_value=99):
        resp = client.delete(f"/admin/listings/{row.id}", headers=HDRS)

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    etsy_mock.delete_listing.assert_called_once()

    db.refresh(row)
    assert row.deleted_at is not None
    assert row.status == "deleted"
