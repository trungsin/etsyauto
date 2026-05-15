"""E2E integration test: full listing-creator workflow (sub-feature C, Phase 5).

Pipeline covered:
  1. Create template (with primary_color + per-size pricing)
  2. POST /templates/{id}/color-bases/{color} for 3 colors
  3. POST /templates/{id}/expand-variations → 9 (3×3) rows
  4. Upload design (RGBA PNG)
  5. POST /composite/preview-all-colors → 3 composite URLs
  6. POST /listings/from-template → mocked Etsy create + inventory + N images
  7. Idempotent re-call → returns same etsy_listing_id
  8. Partial failure: enable a combo for a color whose Etsy taxonomy lookup fails → 422

All external services (Etsy API, R2, remove.bg, httpx GETs of composite URLs) mocked.
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

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.listing import Listing
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "e2e-admin-token-creator"
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
# Image + R2 helpers (reused from test_e2e_template_workflow.py)
# ---------------------------------------------------------------------------

def _make_png_rgba(w: int = 200, h: int = 200, color=(180, 80, 40, 220)) -> bytes:
    img = Image.new("RGBA", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_r2_mock(exists: bool = False) -> MagicMock:
    mock = MagicMock()
    mock.object_exists.return_value = exists
    mock.get_public_url.side_effect = lambda key: f"https://cdn.example.com/{key}"
    mock.upload_image.side_effect = lambda data, key: f"https://cdn.example.com/{key}"
    mock.delete_object.return_value = None
    mock.list_objects.return_value = []
    return mock


def _patch_r2(mock_instance):
    return patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_instance)


# ---------------------------------------------------------------------------
# Etsy mock
# ---------------------------------------------------------------------------

def _make_etsy_client_mock(*, listing_id="42"):
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.get_taxonomy_property_values.side_effect = lambda tid, pid: {
        "results": [
            {"value_id": 1, "name": "White"},
            {"value_id": 2, "name": "Black"},
            {"value_id": 3, "name": "Sand"},
            {"value_id": 100, "name": "S"},
            {"value_id": 101, "name": "M"},
            {"value_id": 102, "name": "L"},
        ]
    }
    mock.create_draft_listing.return_value = {"listing_id": listing_id}
    mock.update_listing_inventory.return_value = {}
    mock.upload_listing_image_bytes.return_value = {"listing_image_id": 1}
    return mock


def _patch_creator_externals(etsy_mock):
    """Patch the externals used by listing_creator_service."""
    return [
        patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy_mock),
        patch(
            "app.services.listing_creator_service.httpx.Client",
            return_value=MagicMock(
                __enter__=MagicMock(
                    return_value=MagicMock(
                        get=MagicMock(return_value=MagicMock(content=b"\x89PNG fake"))
                    )
                ),
                __exit__=MagicMock(return_value=False),
            ),
        ),
        patch("app.services.listing_creator_service.time.sleep", return_value=None),
    ]


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _create_template(client, r2_mock, *, colors=("White", "Black", "Sand"), primary="White") -> int:
    base_png = _make_png_rgba(400, 400)
    options = {
        "sizes": [
            {"name": "S", "price_cents": 1900},
            {"name": "M", "price_cents": 1900},
            {"name": "L", "price_cents": 2200},
        ],
        "colors": list(colors),
        "primary_color": primary,
        "etsy_taxonomy_id": 1209,
    }
    with _patch_r2(r2_mock):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Comfort Tee E2E",
                "category": "apparel",
                "composite_anchor": json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}),
                "default_price_cents": "1900",
                "variation_options": json.dumps(options),
            },
            files={"base_image": ("blank.png", base_png, "image/png")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload_color_bases(client, r2_mock, template_id, colors):
    for color in colors:
        png = _make_png_rgba(400, 400, color=(220, 220, 220, 255))
        with _patch_r2(r2_mock):
            resp = client.post(
                f"/templates/{template_id}/color-bases/{color}",
                headers=VALID_HEADERS,
                files={"base_image": (f"{color}.png", png, "image/png")},
            )
        assert resp.status_code == 200, f"color-base {color}: {resp.text}"


def _upload_design(client, r2_mock) -> int:
    design_png = _make_png_rgba(100, 100)
    with _patch_r2(r2_mock):
        resp = client.post(
            "/designs",
            headers=VALID_HEADERS,
            data={"name": "Star Logo", "source_type": "upload"},
            files={"file": ("star.png", design_png, "image/png")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _fake_urlopen(url):
    """urllib mock returning a tiny RGBA PNG for any URL."""
    return io.BytesIO(_make_png_rgba(200, 200))


# ---------------------------------------------------------------------------
# E2E happy path
# ---------------------------------------------------------------------------

def test_e2e_creator_workflow_happy_path(client, db_session):
    """Create template → 3 color bases → expand variations → design → preview-all-colors
    → from-template create draft. All Etsy + R2 + remove.bg calls mocked."""

    r2_mock = _make_r2_mock(exists=False)
    colors = ("White", "Black", "Sand")

    # 1-2. Template + per-color bases
    tid = _create_template(client, r2_mock, colors=colors, primary="Sand")
    _upload_color_bases(client, r2_mock, tid, colors)

    # 3. Expand variations cartesian (3 sizes × 3 colors = 9 rows)
    resp = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 9

    # 4. Design
    did = _upload_design(client, r2_mock)

    # 5. Preview-all-colors (urlopen mocked so Pillow gets a real PNG)
    r2_miss = _make_r2_mock(exists=False)
    with _patch_r2(r2_miss), patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        resp = client.post(
            "/composite/preview-all-colors",
            headers=VALID_HEADERS,
            json={"template_id": tid, "design_id": did},
        )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 3
    assert {r["color"] for r in results} == set(colors)
    assert all(r.get("composite_url") for r in results)

    # 6. POST /listings/from-template — 3 colors × 3 sizes, but toggle off 1 combo
    # Re-use cached composites by reporting R2 cache hit during from-template flow.
    r2_hit = _make_r2_mock(exists=True)
    etsy = _make_etsy_client_mock(listing_id="9001")
    p1, p2, p3 = _patch_creator_externals(etsy)
    enabled_combos = [
        {"size": s, "color": c, "enabled": not (s == "L" and c == "Black")}
        for s in ("S", "M", "L") for c in colors
    ]
    with p1, p2, p3, _patch_r2(r2_hit):
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": tid, "design_id": did,
                "title": "Custom Comfort Tee", "description": "Hand-printed tee",
                "tags": ["tshirt", "custom", "gift"],
                "shop_id": "shop42",
                "enabled_combos": enabled_combos,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["etsy_listing_id"] == "9001"
    assert body["idempotent"] is False

    # Etsy create called once
    assert etsy.create_draft_listing.call_count == 1
    # Inventory: 9 total - 1 disabled = 8 products
    inv_args = etsy.update_listing_inventory.call_args
    products = inv_args.args[1] if len(inv_args.args) > 1 else inv_args.kwargs["products"]
    assert len(products) == 8

    # Image uploads: 1 universal + 3 per-color (gallery sorted by template_image.rank)
    assert etsy.upload_listing_image_bytes.call_count == 4
    # First upload gets rank=1 (loop counter) regardless of color
    first_call = etsy.upload_listing_image_bytes.call_args_list[0]
    assert first_call.kwargs.get("rank") == 1

    # Local Listing row persisted with template/design link
    rows = list(db_session.scalars(select(Listing)))
    assert len(rows) == 1
    assert rows[0].etsy_listing_id == "9001"
    assert rows[0].template_id == tid
    assert rows[0].design_id == did
    assert rows[0].status == "created"


# ---------------------------------------------------------------------------
# E2E idempotency
# ---------------------------------------------------------------------------

def test_e2e_creator_idempotent_second_call(client, db_session):
    """Second from-template call with same (template, design) returns existing
    etsy_listing_id without re-hitting Etsy."""

    r2_mock = _make_r2_mock(exists=False)
    colors = ("White", "Black")
    tid = _create_template(client, r2_mock, colors=colors, primary="White")
    _upload_color_bases(client, r2_mock, tid, colors)
    did = _upload_design(client, r2_mock)

    body = {
        "template_id": tid, "design_id": did,
        "title": "Tee", "description": "Soft tee",
        "shop_id": "shop1",
        "enabled_combos": [
            {"size": "S", "color": "White"},
            {"size": "S", "color": "Black"},
        ],
    }

    etsy = _make_etsy_client_mock(listing_id="55555")
    p1, p2, p3 = _patch_creator_externals(etsy)
    r2_hit = _make_r2_mock(exists=True)
    with p1, p2, p3, _patch_r2(r2_hit):
        resp1 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)
        resp2 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)

    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["etsy_listing_id"] == resp2.json()["etsy_listing_id"] == "55555"
    assert resp2.json()["idempotent"] is True
    # Etsy.create_draft only called on the first request
    assert etsy.create_draft_listing.call_count == 1
    # Only 1 Listing row persisted
    rows = list(db_session.scalars(select(Listing)))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# E2E partial failure: taxonomy lookup miss → 422, no Listing persisted
# ---------------------------------------------------------------------------

def test_e2e_creator_unknown_color_returns_422_and_no_listing(client, db_session):
    """A combo using a color absent from Etsy's taxonomy values must return 422 and
    leave no Listing row behind (transactional safety)."""

    r2_mock = _make_r2_mock(exists=False)
    colors = ("White", "Mauve")  # Mauve is NOT in mock taxonomy
    tid = _create_template(client, r2_mock, colors=colors, primary="White")
    _upload_color_bases(client, r2_mock, tid, colors)
    did = _upload_design(client, r2_mock)

    etsy = _make_etsy_client_mock()
    p1, p2, p3 = _patch_creator_externals(etsy)
    r2_hit = _make_r2_mock(exists=True)
    with p1, p2, p3, _patch_r2(r2_hit):
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": tid, "design_id": did,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "S", "color": "Mauve"},
                ],
            },
        )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "Mauve" in detail or "no value" in detail.lower()

    # Etsy create_draft never called — taxonomy resolve failed first
    assert etsy.create_draft_listing.call_count == 0
    # No Listing row persisted
    assert list(db_session.scalars(select(Listing))) == []
