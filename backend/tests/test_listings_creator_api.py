"""Tests for sub-feature C Phase 3: POST /listings/from-template + listing_creator_service.

Etsy API + R2 fully mocked — no network.
"""
from __future__ import annotations

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
from app.models.design import Design
from app.models.listing import Listing  # noqa: F401
from app.models.template import Template


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-creator"
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
    # Clear taxonomy cache between tests
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
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_template(session, colors=("White", "Black"), primary="White") -> Template:
    color_bases = {c: f"https://cdn.example.com/templates/{c.lower()}.png" for c in colors}
    options = {
        "sizes": [
            {"name": "S", "price_cents": 1900},
            {"name": "M", "price_cents": 1900},
            {"name": "XL", "price_cents": 2200},
        ],
        "colors": list(colors),
        "primary_color": primary,
        "etsy_taxonomy_id": 1209,
    }
    t = Template(
        name="Comfort Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/templates/default.png",
        composite_anchor_json=json.dumps({"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}),
        default_price_cents=1900,
        variation_options_json=json.dumps(options),
        color_base_images_json=json.dumps(color_bases),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_design(session) -> Design:
    d = Design(
        name="logo",
        source_type="upload",
        file_url="https://cdn.example.com/designs/logo.png",
        width=1000, height=1000,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _make_etsy_client_mock(*, listing_id="9999"):
    """Mock EtsyApiClient with all methods needed by listing_creator_service."""
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    # Taxonomy lookup — return same value names that resolve to deterministic IDs
    mock.get_taxonomy_property_values.side_effect = lambda tid, pid: {
        "results": [
            {"value_id": 1, "name": "White"},
            {"value_id": 2, "name": "Black"},
            {"value_id": 3, "name": "Sand"},
            {"value_id": 100, "name": "S"},
            {"value_id": 101, "name": "M"},
            {"value_id": 102, "name": "XL"},
        ]
    }
    mock.create_draft_listing.return_value = {"listing_id": listing_id}
    mock.update_listing_inventory.return_value = {}
    mock.upload_listing_image_bytes.return_value = {"listing_image_id": 1}
    return mock


def _make_rendered(tid, did, colors):
    """Build render_all_for_listing return value for a legacy template with given colors."""
    # Mirrors _virtualize: rank=0 universal + rank=N per-color
    rows = [
        {"id": None, "template_id": tid, "image_url": f"https://cdn.example.com/templates/default.png",
         "color": None, "rank": 0, "role": "mockup", "is_virtual": True,
         "url": f"https://cdn.example.com/composites/{tid}-{did}-universal.png", "cached": False, "error": None},
    ]
    for i, color in enumerate(colors, start=1):
        rows.append({
            "id": None, "template_id": tid, "image_url": f"https://cdn.example.com/templates/{color.lower()}.png",
            "color": color, "rank": i, "role": "mockup", "is_virtual": True,
            "url": f"https://cdn.example.com/composites/{tid}-{did}-{color}.png", "cached": False, "error": None,
        })
    return rows


def _patch_externals(etsy_mock, colors=("White", "Black")):
    """Combo patch for EtsyApiClient + composite + httpx + sleep used by service."""
    return [
        patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy_mock),
        patch(
            "app.services.listing_creator_service.composite_service.render_all_for_listing",
            side_effect=lambda s, tid, did, zone_designs=None: _make_rendered(tid, did, colors),
        ),
        patch(
            "app.services.listing_creator_service.httpx.Client",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=MagicMock(get=MagicMock(return_value=MagicMock(content=b"\x89PNG fake")))),
                __exit__=MagicMock(return_value=False),
            ),
        ),
        patch("app.services.listing_creator_service.time.sleep", return_value=None),
    ]


# ---------------------------------------------------------------------------
# Auth + validation
# ---------------------------------------------------------------------------

def test_from_template_requires_token(client):
    resp = client.post("/listings/from-template", json={
        "template_id": 1, "design_id": 1,
        "title": "x", "description": "y",
        "shop_id": "1",
        "enabled_combos": [{"size": "S", "color": "White"}],
    })
    assert resp.status_code == 401


def test_from_template_template_not_found(client):
    etsy = _make_etsy_client_mock()
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": 9999, "design_id": 9999,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )
    assert resp.status_code == 404


def test_from_template_rejects_reference_only_design(client, db_session):
    template = _seed_template(db_session)
    design = Design(
        name="ref", source_type="reference_only",
        file_url="https://cdn.example.com/designs/ref.png",
        width=100, height=100,
    )
    db_session.add(design)
    db_session.commit()
    db_session.refresh(design)

    etsy = _make_etsy_client_mock()
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )
    assert resp.status_code == 400
    assert "reference_only" in resp.json()["detail"]


def test_from_template_invalid_combo_size(client, db_session):
    template = _seed_template(db_session)
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock()
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "XXXL", "color": "White"}],  # not in template sizes
            },
        )
    assert resp.status_code == 400
    assert "size" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_from_template_happy_path(client, db_session):
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42424242")
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Custom Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt", "custom", "gift"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "M", "color": "White"},
                    {"size": "XL", "color": "Black"},
                ],
            },
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["etsy_listing_id"] == "42424242"
    assert body["draft_url"] == "https://www.etsy.com/your/shops/me/tools/listings/42424242"
    assert body["idempotent"] is False

    # Etsy was called exactly once for create + inventory
    assert etsy.create_draft_listing.call_count == 1
    assert etsy.update_listing_inventory.call_count == 1

    # Inventory products carry size + color property values
    inv_call = etsy.update_listing_inventory.call_args
    products = inv_call.args[1] if len(inv_call.args) > 1 else inv_call.kwargs["products"]
    assert len(products) == 3
    for p in products:
        prop_ids = {pv["property_id"] for pv in p["property_values"]}
        from app.services.etsy_taxonomy import PROPERTY_SIZE, PROPERTY_PRIMARY_COLOR
        assert PROPERTY_SIZE in prop_ids and PROPERTY_PRIMARY_COLOR in prop_ids

    # Image upload called once per gallery image (1 universal + 2 per-color = 3)
    assert etsy.upload_listing_image_bytes.call_count == 3

    # Listing row persisted with template/design link
    listings = list(db_session.scalars(__import__("sqlalchemy").select(Listing)))
    assert len(listings) == 1
    assert listings[0].etsy_listing_id == "42424242"
    assert listings[0].template_id == template.id
    assert listings[0].design_id == design.id


def test_from_template_is_idempotent(client, db_session):
    template = _seed_template(db_session)
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="11111")
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        body = {
            "template_id": template.id, "design_id": design.id,
            "title": "x", "description": "y",
            "shop_id": "shop1",
            "enabled_combos": [{"size": "S", "color": "White"}],
        }
        resp1 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)
        resp2 = client.post("/listings/from-template", headers=VALID_HEADERS, json=body)

    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["etsy_listing_id"] == resp2.json()["etsy_listing_id"]
    assert resp2.json()["idempotent"] is True
    # Second call did NOT hit Etsy
    assert etsy.create_draft_listing.call_count == 1


def test_from_template_image_rank_primary_first(client, db_session):
    """Gallery images uploaded in template_image.rank order (universal first, then per-color)."""
    template = _seed_template(db_session, colors=("White", "Black", "Sand"), primary="Sand")
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="555")
    # Pass all 3 colors so render mock returns 4 images: universal + White + Black + Sand
    patches = _patch_externals(etsy, colors=("White", "Black", "Sand"))
    with patches[0], patches[1], patches[2], patches[3]:
        client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "S", "color": "Black"},
                    {"size": "S", "color": "Sand"},
                ],
            },
        )

    # 4 uploads: universal (rank=0) + 3 per-color; uploaded with sequential rank 1..4
    rank_calls = [c.kwargs.get("rank") or c.args[-1] for c in etsy.upload_listing_image_bytes.call_args_list]
    assert rank_calls == [1, 2, 3, 4]

    # Filenames are img-{id or loop_rank}.png — id=None for virtual rows so uses loop rank
    first_call_kwargs = etsy.upload_listing_image_bytes.call_args_list[0].kwargs
    assert first_call_kwargs.get("filename", "").startswith("img-")


# ---------------------------------------------------------------------------
# Taxonomy lookup
# ---------------------------------------------------------------------------

def test_taxonomy_resolve_caches_results(client, db_session):
    """2nd call with same template+taxonomy doesn't re-fetch property values."""
    template = _seed_template(db_session)
    design = _seed_design(db_session)
    other_design = Design(
        name="logo2", source_type="upload",
        file_url="https://cdn.example.com/designs/logo2.png",
        width=1000, height=1000,
    )
    db_session.add(other_design)
    db_session.commit()
    db_session.refresh(other_design)

    etsy = _make_etsy_client_mock(listing_id="111")
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        for did in (design.id, other_design.id):
            etsy.create_draft_listing.return_value = {"listing_id": str(100 + did)}
            client.post(
                "/listings/from-template",
                headers=VALID_HEADERS,
                json={
                    "template_id": template.id, "design_id": did,
                    "title": "x", "description": "y",
                    "shop_id": "shop1",
                    "enabled_combos": [{"size": "S", "color": "White"}],
                },
            )

    # 2 properties (color + size) × 1 taxonomy = 2 total fetches across BOTH listings
    # because cache persists per (taxonomy_id, property_id).
    assert etsy.get_taxonomy_property_values.call_count == 2


# ---------------------------------------------------------------------------
# Default shop_id auto-resolution
# ---------------------------------------------------------------------------

def test_from_template_omitted_shop_id_uses_connected(client, db_session):
    """When shop_id is absent, route resolves it from the connected Etsy credential."""
    from app.models.api_credential import ApiCredential

    db_session.add(ApiCredential(
        provider="etsy",
        oauth_token="123456.tok",
        refresh_token="r",
        shop_id="77777777",
    ))
    db_session.commit()

    template = _seed_template(db_session)
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42")
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                # no shop_id
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )

    assert resp.status_code == 201, resp.text
    # Service called with the cached shop_id, not None
    assert etsy.create_draft_listing.call_args.args[0] == "77777777"


def test_from_template_no_shop_no_connection_returns_409(client, db_session):
    """No body shop_id + no connected Etsy account → clear 409."""
    template = _seed_template(db_session)
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock()
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                "enabled_combos": [{"size": "S", "color": "White"}],
            },
        )

    assert resp.status_code == 409
    assert "No Etsy shop connected" in resp.json()["detail"]


def test_taxonomy_unknown_value_raises(client, db_session):
    """If template uses a color that Etsy taxonomy doesn't know, return 422."""
    template = _seed_template(db_session, colors=("White", "Mauve"))  # Mauve not in mock taxonomy
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock()
    patches = _patch_externals(etsy)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "x", "description": "y",
                "shop_id": "shop1",
                "enabled_combos": [{"size": "S", "color": "Mauve"}],
            },
        )
    assert resp.status_code == 422
    assert "Mauve" in resp.json()["detail"] or "no value" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Image pool integration tests
# ---------------------------------------------------------------------------


def test_create_uses_template_images_pool(client, db_session):
    """Listing creator calls render_all_for_listing when template has image pool."""
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    # Seed template image pool
    from app.services import template_image_service
    template_image_service.create(
        db_session, template_id=template.id,
        image_url="https://cdn.example.com/img1.png", rank=0, color=None
    )
    template_image_service.create(
        db_session, template_id=template.id,
        image_url="https://cdn.example.com/img2.png", rank=1, color="White"
    )
    template_image_service.create(
        db_session, template_id=template.id,
        image_url="https://cdn.example.com/img3.png", rank=2, color="Black"
    )

    etsy = _make_etsy_client_mock(listing_id="42424242")

    # Mock render_all_for_listing to capture call args
    render_all_calls = []

    def capture_render_all(session, tid, did, zone_designs=None):
        render_all_calls.append({"tid": tid, "did": did, "zone_designs": zone_designs})
        return _make_rendered(tid, did, ("White", "Black"))

    patches = [
        patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy),
        patch(
            "app.services.listing_creator_service.composite_service.render_all_for_listing",
            side_effect=capture_render_all,
        ),
        patch("app.services.listing_creator_service.httpx.Client"),
        patch("time.sleep"),
    ]

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "M", "color": "Black"},
                ],
            },
        )

    assert resp.status_code == 201
    # Verify render_all_for_listing was called with correct args
    assert len(render_all_calls) == 1
    assert render_all_calls[0]["tid"] == template.id
    assert render_all_calls[0]["did"] == design.id


def test_create_uploads_top_10_by_rank(client, db_session):
    """Only top 10 images by rank are uploaded to Etsy."""
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    # Seed 12 template images
    from app.services import template_image_service
    for i in range(12):
        template_image_service.create(
            db_session, template_id=template.id,
            image_url=f"https://cdn.example.com/img{i}.png", rank=i
        )

    etsy = _make_etsy_client_mock(listing_id="42424242")

    # Mock render_all to return all 12 images
    def render_all_12(session, tid, did, zone_designs=None):
        return [
            {
                "id": i, "template_id": tid, "image_url": f"https://cdn.example.com/img{i}.png",
                "color": None, "rank": i, "role": "mockup", "is_virtual": False,
                "url": f"https://cdn.example.com/composites/{i}.png", "cached": False, "error": None
            }
            for i in range(12)
        ]

    patches = [
        patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy),
        patch(
            "app.services.listing_creator_service.composite_service.render_all_for_listing",
            side_effect=render_all_12,
        ),
        patch("app.services.listing_creator_service.httpx.Client"),
        patch("time.sleep"),
    ]

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                ],
            },
        )

    assert resp.status_code == 201
    # Should have uploaded exactly 10 images
    assert etsy.upload_listing_image_bytes.call_count == 10


def test_create_binds_variation_images_for_per_color(client, db_session):
    """set_variation_images is called with correct value_to_image_id map."""
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42424242")
    patches = _patch_externals(etsy)

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "M", "color": "Black"},
                ],
            },
        )

    assert resp.status_code == 201
    # set_variation_images should have been called
    assert etsy.set_variation_images.call_count >= 1


def test_create_handles_single_color_with_multiple_sizes(client, db_session):
    """Variation images are set even with single color (no multi-color case)."""
    template = _seed_template(db_session, colors=("White",))
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42424242")
    patches = _patch_externals(etsy, colors=("White",))

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                    {"size": "M", "color": "White"},
                ],
            },
        )

    assert resp.status_code == 201
    # Single color still has property value mapping
    assert etsy.set_variation_images.call_count >= 1


def test_create_legacy_template_uses_virtualization(client, db_session):
    """Templates without image pool still work (virtualized)."""
    # Template with no template_images rows
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42424242")
    patches = _patch_externals(etsy)

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                ],
            },
        )

    assert resp.status_code == 201
    # Should still have created listing with virtualized images
    assert etsy.upload_listing_image_bytes.call_count >= 1


def test_create_partial_image_failure_logs_and_continues(client, db_session):
    """One image failure doesn't stop listing creation."""
    template = _seed_template(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    etsy = _make_etsy_client_mock(listing_id="42424242")

    # Mock render_all to return one image with error
    def render_all_with_error(session, tid, did, zone_designs=None):
        return [
            {
                "id": 1, "template_id": tid, "image_url": "https://cdn.example.com/img1.png",
                "color": None, "rank": 0, "role": "mockup", "is_virtual": False,
                "url": "https://cdn.example.com/composites/1.png", "cached": False, "error": None
            },
            {
                "id": 2, "template_id": tid, "image_url": "https://cdn.example.com/img2.png",
                "color": "White", "rank": 1, "role": "mockup", "is_virtual": False,
                "url": None, "cached": False, "error": "Download failed"
            },
        ]

    patches = [
        patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy),
        patch(
            "app.services.listing_creator_service.composite_service.render_all_for_listing",
            side_effect=render_all_with_error,
        ),
        patch("app.services.listing_creator_service.httpx.Client"),
        patch("time.sleep"),
    ]

    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/listings/from-template",
            headers=VALID_HEADERS,
            json={
                "template_id": template.id, "design_id": design.id,
                "title": "Comfort Tee", "description": "Hand-printed",
                "tags": ["t-shirt"],
                "shop_id": "shop42",
                "enabled_combos": [
                    {"size": "S", "color": "White"},
                ],
            },
        )

    assert resp.status_code == 201
    # Only successful image was uploaded
    assert etsy.upload_listing_image_bytes.call_count == 1
