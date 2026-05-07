"""Tests for Listing Creator Admin UI (sub-feature C, v0.5.0).

Routes covered (all under prefix /admin/listings):
  GET  /creator                 → full page
  GET  /creator/template-info   → HTMX partial: matrix + colors
  POST /creator/preview         → HTMX partial: composite thumbnails
  POST /creator/submit          → HTMX partial: result toast (success/error/idempotent)

External services (composite_service, listing_creator_service) mocked.
"""
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
from app.models.design import Design  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.template import Template


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-creator-ui"
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


def _seed_template(session, *, sizes=("S", "M", "L"), colors=("White", "Black"),
                   primary="White") -> Template:
    options = {
        "sizes": [{"name": s, "price_cents": 1900} for s in sizes],
        "colors": list(colors),
        "primary_color": primary,
    }
    color_bases = {c: f"https://cdn.example.com/templates/{c.lower()}.png" for c in colors}
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
        name="Star Logo",
        source_type="upload",
        file_url="https://cdn.example.com/designs/star.png",
        width=1000, height=1000,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


# ---------------------------------------------------------------------------
# Auth + page render
# ---------------------------------------------------------------------------

def test_creator_page_requires_token(client):
    resp = client.get("/admin/listings/creator")
    assert resp.status_code == 401


def test_creator_page_renders_with_dropdowns(client, db_session):
    _seed_template(db_session)
    _seed_design(db_session)
    resp = client.get("/admin/listings/creator", headers=VALID_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Listing Creator" in body
    assert 'name="template_id"' in body
    assert 'name="design_id"' in body
    assert 'hx-post="/admin/listings/creator/submit"' in body


def test_creator_page_filters_templates_without_colors(client, db_session):
    """Template with empty colors list should NOT appear in dropdown."""
    no_color = Template(
        name="NoColors",
        category="apparel",
        base_image_url="https://cdn.example.com/templates/x.png",
        composite_anchor_json=json.dumps({"x": 0, "y": 0, "w": 1, "h": 1}),
        default_price_cents=1000,
        variation_options_json=json.dumps({"sizes": ["S"], "colors": []}),
    )
    db_session.add(no_color)
    db_session.commit()
    _seed_template(db_session, colors=("Red", "Blue"))
    resp = client.get("/admin/listings/creator", headers=VALID_HEADERS)
    assert "Comfort Tee" in resp.text
    assert "NoColors" not in resp.text


# ---------------------------------------------------------------------------
# template-info partial
# ---------------------------------------------------------------------------

def test_template_info_partial_renders_matrix(client, db_session):
    t = _seed_template(db_session, sizes=("S", "M", "L"), colors=("White", "Black"),
                       primary="White")
    resp = client.get(
        f"/admin/listings/creator/template-info?template_id={t.id}",
        headers={**VALID_HEADERS, "HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    # 3 sizes × 2 colors = 6 checkboxes (+ 12 hidden size/color inputs)
    assert body.count('type="checkbox"') == 6
    # Indexed encoding: combo_<i>_<j> + matching hidden size/color
    assert 'name="combo_0_0"' in body
    assert 'name="combo_size_0_0" value="S"' in body
    assert 'name="combo_color_0_0" value="White"' in body
    assert 'name="combo_2_1"' in body  # last cell (L, Black)
    # primary color marked with star
    assert "⭐" in body


def test_template_info_404_for_missing_template(client):
    resp = client.get(
        "/admin/listings/creator/template-info?template_id=9999",
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# preview partial
# ---------------------------------------------------------------------------

def test_preview_calls_composite_service(client, db_session):
    t = _seed_template(db_session)
    d = _seed_design(db_session)
    fake_results = [
        {"color": "White", "composite_url": "https://cdn.example.com/c/1-1-White.png",
         "cached": False, "error": None},
        {"color": "Black", "composite_url": "https://cdn.example.com/c/1-1-Black.png",
         "cached": True, "error": None},
    ]
    with patch(
        "app.routes.templates_admin.composite_service.get_or_create_composites_all_colors",
        return_value=fake_results,
    ) as mock_fn:
        resp = client.post(
            "/admin/listings/creator/preview",
            headers=VALID_HEADERS,
            data={"template_id": t.id, "design_id": d.id},
        )
    assert resp.status_code == 200, resp.text
    mock_fn.assert_called_once_with(session=mock_fn.call_args.kwargs["session"],
                                    template_id=t.id, design_id=d.id)
    assert "https://cdn.example.com/c/1-1-White.png" in resp.text
    assert "⚡" in resp.text  # cached marker


def test_preview_handles_value_error_as_400(client, db_session):
    t = _seed_template(db_session)
    d = _seed_design(db_session)
    with patch(
        "app.routes.templates_admin.composite_service.get_or_create_composites_all_colors",
        side_effect=ValueError("Template has no colors defined"),
    ):
        resp = client.post(
            "/admin/listings/creator/preview",
            headers=VALID_HEADERS,
            data={"template_id": t.id, "design_id": d.id},
        )
    assert resp.status_code == 400
    assert "no colors" in resp.text


# ---------------------------------------------------------------------------
# submit partial
# ---------------------------------------------------------------------------

def test_submit_parses_enabled_combos_and_calls_service(client, db_session):
    t = _seed_template(db_session, colors=("White", "Black"))
    d = _seed_design(db_session)
    fake_result = {
        "listing_id": 1,
        "etsy_listing_id": "9001",
        "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/9001",
        "composite_urls": [],
        "idempotent": False,
    }
    with patch(
        "app.routes.templates_admin.listing_creator_service.create_from_template",
        return_value=fake_result,
    ) as mock_fn:
        resp = client.post(
            "/admin/listings/creator/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": t.id,
                "design_id": d.id,
                "shop_id": "shop42",
                "title": "Custom Tee",
                "description": "Soft hand-printed",
                "tags_csv": "tshirt, custom, gift",
                "combo_0_0": "on",
                "combo_size_0_0": "S", "combo_color_0_0": "White",
                "combo_1_1": "on",
                "combo_size_1_1": "M", "combo_color_1_1": "Black",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "9001" in body
    assert "Open in Etsy" in body

    call_kwargs = mock_fn.call_args.kwargs
    combos = call_kwargs["enabled_combos"]
    assert len(combos) == 2
    assert {(c["size"], c["color"]) for c in combos} == {("S", "White"), ("M", "Black")}
    assert call_kwargs["tags"] == ["tshirt", "custom", "gift"]
    assert call_kwargs["shop_id"] == "shop42"


def test_submit_renders_idempotent_badge(client, db_session):
    t = _seed_template(db_session)
    d = _seed_design(db_session)
    fake_result = {
        "listing_id": 1, "etsy_listing_id": "9999",
        "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/9999",
        "composite_urls": [], "idempotent": True,
    }
    with patch(
        "app.routes.templates_admin.listing_creator_service.create_from_template",
        return_value=fake_result,
    ):
        resp = client.post(
            "/admin/listings/creator/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": t.id, "design_id": d.id, "shop_id": "shop1",
                "title": "Tee", "description": "desc",
                "combo_0_0": "on",
                "combo_size_0_0": "S", "combo_color_0_0": "White",
            },
        )
    assert resp.status_code == 200
    assert "Already created" in resp.text


def test_submit_rejects_no_enabled_combos(client, db_session):
    t = _seed_template(db_session)
    d = _seed_design(db_session)
    resp = client.post(
        "/admin/listings/creator/submit",
        headers=VALID_HEADERS,
        data={
            "template_id": t.id, "design_id": d.id, "shop_id": "shop1",
            "title": "Tee", "description": "desc",
            # no combo_* fields
        },
    )
    assert resp.status_code == 422
    assert "No enabled combos" in resp.text


def test_submit_handles_names_with_underscores(client, db_session):
    """Indexed encoding survives size/color names containing arbitrary chars
    (regression: legacy `__` separator collided on names like `"Light__Blue"`)."""
    t = _seed_template(db_session, sizes=("S", "M"), colors=("Light__Blue", "Hot Pink"))
    d = _seed_design(db_session)
    fake_result = {
        "listing_id": 1, "etsy_listing_id": "777",
        "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/777",
        "composite_urls": [], "idempotent": False,
    }
    with patch(
        "app.routes.templates_admin.listing_creator_service.create_from_template",
        return_value=fake_result,
    ) as mock_fn:
        resp = client.post(
            "/admin/listings/creator/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": t.id, "design_id": d.id, "shop_id": "shop1",
                "title": "Tee", "description": "desc",
                "combo_0_0": "on",
                "combo_size_0_0": "S", "combo_color_0_0": "Light__Blue",
                "combo_1_1": "on",
                "combo_size_1_1": "M", "combo_color_1_1": "Hot Pink",
            },
        )
    assert resp.status_code == 200, resp.text
    combos = mock_fn.call_args.kwargs["enabled_combos"]
    pairs = {(c["size"], c["color"]) for c in combos}
    assert pairs == {("S", "Light__Blue"), ("M", "Hot Pink")}


def test_submit_rejects_missing_required_fields(client, db_session):
    t = _seed_template(db_session)
    d = _seed_design(db_session)
    resp = client.post(
        "/admin/listings/creator/submit",
        headers=VALID_HEADERS,
        data={
            "template_id": t.id, "design_id": d.id,
            # shop_id, title, description missing
            "combo_0_0": "on",
            "combo_size_0_0": "S", "combo_color_0_0": "White",
        },
    )
    assert resp.status_code == 422
    assert "Missing required" in resp.text
