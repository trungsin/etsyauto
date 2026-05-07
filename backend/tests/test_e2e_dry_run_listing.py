"""End-to-end test: full listing-creator pipeline under ETSY_DRY_RUN=true.

No real Etsy HTTP fired. Confirms the pipeline glues together:
  POST /listings/from-template
    → composite render (mocked R2 cache hit)
    → EtsyApiClient.create_draft_listing → dry-run fixture
    → update_listing_inventory → dry-run fixture
    → upload_listing_image_bytes → dry-run fixture
    → Listing row persisted with etsy_listing_id from fixture
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.listing import Listing
from app.models.template import Template


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "e2e-dry-run-token"
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
    from app.services import etsy_taxonomy
    etsy_taxonomy.clear_cache()
    yield


@pytest.fixture
def dry_run_client(monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    monkeypatch.setattr(settings, "admin_token", ADMIN_TOKEN)
    settings.r2_public_url = "https://cdn.example.com"
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    s = _TestingSessionLocal()
    yield s
    s.close()


def _seed(session):
    options = {
        "sizes": [{"name": "S", "price_cents": 1900}],
        "colors": ["White"],
        "primary_color": "White",
    }
    t = Template(
        name="Dry-Run Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/blank.png",
        composite_anchor_json=json.dumps({"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}),
        default_price_cents=1900,
        variation_options_json=json.dumps(options),
        color_base_images_json=json.dumps({"White": "https://cdn.example.com/w.png"}),
    )
    d = Design(
        name="Star", source_type="upload",
        file_url="https://cdn.example.com/star.png",
        width=1000, height=1000,
    )
    session.add_all([t, d])
    session.commit()
    session.refresh(t)
    session.refresh(d)
    return t, d


def _r2_hit_mock() -> MagicMock:
    """R2 always reports cache hit so composite render is skipped."""
    m = MagicMock()
    m.object_exists.return_value = True
    m.get_public_url.side_effect = lambda key: f"https://cdn.example.com/{key}"
    m.upload_image.side_effect = lambda data, key: f"https://cdn.example.com/{key}"
    m.delete_object.return_value = None
    m.list_objects.return_value = []
    return m


def _patch_r2(mock):
    return patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock)


# ---------------------------------------------------------------------------
# Happy scenario — full flow
# ---------------------------------------------------------------------------

def test_e2e_dry_run_happy_creates_listing_record(dry_run_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    t, d = _seed(db_session)
    with _patch_r2(_r2_hit_mock()), \
         patch("app.services.listing_creator_service.time.sleep", return_value=None), \
         patch("app.services.listing_creator_service.httpx.Client",
               return_value=MagicMock(
                   __enter__=MagicMock(return_value=MagicMock(
                       get=MagicMock(return_value=MagicMock(content=b"\x89PNG fake"))
                   )),
                   __exit__=MagicMock(return_value=False),
               )):
        resp = dry_run_client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": t.id, "design_id": d.id,
                "title": "Tee", "description": "soft",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Etsy fixture returns listing_id=9001 in happy
    assert body["etsy_listing_id"] == "9001"
    assert resp.headers.get("X-Request-ID")  # correlation id echoed

    rows = list(db_session.scalars(select(Listing)))
    assert len(rows) == 1
    assert rows[0].etsy_listing_id == "9001"


# ---------------------------------------------------------------------------
# Auth-fail scenario — friendly toast via error mapper
# ---------------------------------------------------------------------------

def test_e2e_dry_run_auth_fail_returns_502_friendly(dry_run_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "auth_fail")
    t, d = _seed(db_session)
    with _patch_r2(_r2_hit_mock()), \
         patch("app.services.listing_creator_service.time.sleep", return_value=None), \
         patch("app.services.listing_creator_service.httpx.Client",
               return_value=MagicMock(
                   __enter__=MagicMock(return_value=MagicMock(
                       get=MagicMock(return_value=MagicMock(content=b"\x89PNG fake"))
                   )),
                   __exit__=MagicMock(return_value=False),
               )):
        resp = dry_run_client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": t.id, "design_id": d.id,
                "title": "Tee", "description": "soft",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert "login" in detail.lower() or "expired" in detail.lower()
    # No Listing should be persisted on Etsy auth failure
    assert list(db_session.scalars(select(Listing))) == []
