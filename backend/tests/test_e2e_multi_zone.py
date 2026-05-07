"""E2E tests for multi-zone composites (anchor schema v2).

Verifies:
1. v1 templates still composite correctly (regression)
2. v2 multi-zone template + zone_designs map renders + creates Etsy draft
3. Idempotent re-call returns same etsy_listing_id
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
from app.models.template import Template


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "e2e-multi-zone-token"
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


def _make_png_rgba(w: int = 200, h: int = 200) -> bytes:
    img = Image.new("RGBA", (w, h), (180, 80, 40, 220))
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


def _make_etsy_client_mock():
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.get_taxonomy_property_values.side_effect = lambda tid, pid: {
        "results": [
            {"value_id": 1, "name": "White"},
            {"value_id": 2, "name": "Black"},
            {"value_id": 100, "name": "S"},
        ]
    }
    m.create_draft_listing.return_value = {"listing_id": "9001"}
    m.update_listing_inventory.return_value = {}
    m.upload_listing_image_bytes.return_value = {"listing_image_id": 1}
    return m


def _patch_creator_externals(etsy_mock):
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
# Regression: v1 anchor still composites unchanged
# ---------------------------------------------------------------------------

def test_v1_template_renders_through_zone_pipeline(db_session):
    """A v1 single-rect anchor should produce a composite via the new pipeline
    without breaking anything."""
    from app.services import composite_service

    t = Template(
        name="Legacy v1",
        category="apparel",
        base_image_url="https://cdn.example.com/blank.png",
        composite_anchor_json=json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}),
        default_price_cents=1500,
        variation_options_json=json.dumps({"sizes": ["S"], "colors": ["White"]}),
        color_base_images_json=json.dumps({}),
    )
    d = Design(
        name="Star", source_type="upload",
        file_url="https://cdn.example.com/star.png", width=100, height=100,
    )
    db_session.add_all([t, d])
    db_session.commit()
    db_session.refresh(t)
    db_session.refresh(d)

    r2 = _make_r2_mock(exists=False)
    fake_png = _make_png_rgba()
    with _patch_r2(r2), patch("urllib.request.urlopen",
                              side_effect=lambda url: io.BytesIO(fake_png)):
        url, cached = composite_service.get_or_create_composite(
            db_session, t.id, d.id, color=None,
        )
    assert cached is False
    assert url is not None
    # Cache key matches legacy single-zone shape
    assert r2.upload_image.call_args.args[1] == f"composites/{t.id}-{d.id}.png"


# ---------------------------------------------------------------------------
# v2 multi-zone full flow
# ---------------------------------------------------------------------------

def _seed_v2_multi_zone(session, *, design_a_id=None, design_b_id=None) -> Template:
    """Seed a template with v2 anchor: front quad + back rect."""
    anchor_v2 = {
        "version": 2,
        "zones": [
            {
                "name": "front", "kind": "quad",
                "points": [[0.20, 0.20], [0.80, 0.22], [0.78, 0.78], [0.22, 0.76]],
            },
            {
                "name": "back", "kind": "rect",
                "x": 0.30, "y": 0.30, "w": 0.40, "h": 0.30,
            },
        ],
    }
    options = {
        "sizes": [{"name": "S", "price_cents": 1900}],
        "colors": ["White"],
        "primary_color": "White",
    }
    color_bases = {"White": "https://cdn.example.com/white.png"}
    t = Template(
        name="MultiZone Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/blank.png",
        composite_anchor_json=json.dumps(anchor_v2),
        default_price_cents=1900,
        variation_options_json=json.dumps(options),
        color_base_images_json=json.dumps(color_bases),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_design(session, name: str) -> Design:
    d = Design(
        name=name, source_type="upload",
        file_url=f"https://cdn.example.com/{name.lower()}.png",
        width=200, height=200,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def test_e2e_multi_zone_composite_uses_distinct_cache_key(client, db_session):
    """Calling get_or_create_composite with a distinct zone_designs map yields a
    different cache key than the single-design call."""
    from app.services import composite_service

    t = _seed_v2_multi_zone(db_session)
    d_front = _seed_design(db_session, "front_design")
    d_back = _seed_design(db_session, "back_design")

    fake_png = _make_png_rgba()
    r2 = _make_r2_mock(exists=False)
    with _patch_r2(r2), patch("urllib.request.urlopen",
                              side_effect=lambda url: io.BytesIO(fake_png)):
        composite_service.get_or_create_composite(
            db_session, t.id, d_front.id, color="White",
            zone_designs={"front": d_front.id, "back": d_back.id},
        )

    upload_key = r2.upload_image.call_args.args[1]
    # Multi-zone keys end with -multi.png
    assert upload_key.endswith("-multi.png")
    assert "White" in upload_key
    assert str(t.id) in upload_key


def test_e2e_multi_zone_listing_creator_full_flow(client, db_session):
    """POST /listings/from-template with zone_designs threads through to Etsy mock."""
    t = _seed_v2_multi_zone(db_session)
    d_front = _seed_design(db_session, "front_design")
    d_back = _seed_design(db_session, "back_design")

    etsy = _make_etsy_client_mock()
    p1, p2, p3 = _patch_creator_externals(etsy)
    r2_hit = _make_r2_mock(exists=True)  # short-circuit composite render

    with p1, p2, p3, _patch_r2(r2_hit):
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": t.id,
                "design_id": d_front.id,
                "title": "Front + Back Tee",
                "description": "Hand-printed both sides",
                "tags": ["tshirt"],
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "White"}],
                "zone_designs": {"front": d_front.id, "back": d_back.id},
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["etsy_listing_id"] == "9001"
    assert etsy.create_draft_listing.call_count == 1

    # Listing row persisted
    rows = list(db_session.scalars(select(Listing)))
    assert len(rows) == 1
    assert rows[0].template_id == t.id


def test_e2e_multi_zone_idempotent(client, db_session):
    """Same (template, design) re-submit returns same etsy_listing_id."""
    t = _seed_v2_multi_zone(db_session)
    d_front = _seed_design(db_session, "front_design")
    d_back = _seed_design(db_session, "back_design")

    body = {
        "template_id": t.id,
        "design_id": d_front.id,
        "title": "Tee", "description": "desc",
        "shop_id": "shop1",
        "enabled_combos": [{"size": "S", "color": "White"}],
        "zone_designs": {"front": d_front.id, "back": d_back.id},
    }

    etsy = _make_etsy_client_mock()
    p1, p2, p3 = _patch_creator_externals(etsy)
    r2_hit = _make_r2_mock(exists=True)
    with p1, p2, p3, _patch_r2(r2_hit):
        r1 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)
        r2 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)

    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["etsy_listing_id"] == r2.json()["etsy_listing_id"]
    assert r2.json()["idempotent"] is True
    assert etsy.create_draft_listing.call_count == 1
