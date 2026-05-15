"""Smoke tests for the /admin/listings HTML pages (v0.10 UI)."""
from __future__ import annotations

import json

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

ADMIN_TOKEN = "ui-admin-token"
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


def _seed_listing(db, *, status="new") -> Listing:
    t = Template(
        name="UI Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/b.png",
        default_price_cents=1900,
        variation_options_json=json.dumps({
            "sizes": [{"name": "S", "price_cents": 1900}],
            "colors": ["White"],
            "primary_color": "White",
        }),
    )
    db.add(t)
    d = Design(name="logo", source_type="upload",
               file_url="https://cdn.example.com/d.png", width=10, height=10)
    db.add(d)
    db.commit()
    db.refresh(t)
    db.refresh(d)
    payload = {
        "title": "UI Title", "description": "ui desc", "tags": ["a"],
        "enabled_combos": [{"size": "S", "color": "White"}],
        "gallery_snapshot": [
            {"rank": 1, "color": "White", "url": "https://cdn.example.com/ui-composite.png", "id": 1},
        ],
    }
    row = Listing(
        etsy_listing_id=None,
        original_title="UI Title",
        original_desc="ui desc",
        original_tags=json.dumps(["a"]),
        original_images=json.dumps(["https://cdn.example.com/ui-composite.png"]),
        status=status,
        template_id=t.id,
        design_id=d.id,
        local_payload_json=json.dumps(payload),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_detail_page_renders_composite_gallery_and_form(client, db):
    row = _seed_listing(db, status="new")
    resp = client.get(f"/admin/listings/{row.id}", headers=HDRS)
    assert resp.status_code == 200
    body = resp.text
    # Composite image present in gallery
    assert "https://cdn.example.com/ui-composite.png" in body
    # Edit form scaffold present (markers from detail.html)
    assert 'id="lst-edit-form"' in body
    assert 'id="btn-save-edit"' in body
    # Action bar markers
    assert 'id="btn-rerender"' in body
    assert 'id="btn-delete"' in body


def test_detail_status_badge_class_matches_status(client, db):
    row = _seed_listing(db, status="failed")
    resp = client.get(f"/admin/listings/{row.id}", headers=HDRS)
    assert resp.status_code == 200
    assert "badge-failed" in resp.text


def test_base_nav_includes_listings_link(client, db):
    """The Listings link must appear in the global admin nav."""
    resp = client.get("/admin/listings", headers=HDRS)
    assert resp.status_code == 200
    # Nav rendered from base.html
    assert 'href="/admin/listings"' in resp.text
    assert ">Listings<" in resp.text
