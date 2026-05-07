"""Tests for /admin/templates/{id}/anchor GET + POST (Anchor Editor v0.7.1).

Routes covered:
  GET  /admin/templates/{id}/anchor  → renders anchor-editor.html with initial quad points
  POST /admin/templates/{id}/anchor  → validates + persists v2 JSON, invalidates cache

External services (R2StorageClient) mocked so no real network calls are made.
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
from app.models.template import Template

# ---------------------------------------------------------------------------
# DB fixtures (self-contained — no shared conftest)
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "anchor-editor-test-token"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Drop and recreate schema before every test for full isolation."""
    from app.models.template import Template  # noqa: F401 — registers table
    from app.models.template_variation import TemplateVariation  # noqa: F401
    from app.models.design import Design  # noqa: F401
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
def db_session():
    s = _TestingSessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_template(session, *, anchor_json: str | None = None) -> Template:
    """Insert a minimal Template row, optionally with a custom anchor JSON."""
    t = Template(
        name="Test Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/base.png",
        composite_anchor_json=anchor_json or json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}),
        default_price_cents=1900,
        variation_options_json=json.dumps({}),
        color_base_images_json=json.dumps({}),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _fake_invalidate(template_id: int) -> int:
    """No-op replacement for composite_service.invalidate_composites_for_template."""
    return 0


# ---------------------------------------------------------------------------
# GET /admin/templates/{id}/anchor
# ---------------------------------------------------------------------------

def test_anchor_editor_page_renders(client, db_session):
    """GET returns 200 HTML containing the SVG overlay element and the initial points script."""
    tmpl = _seed_template(db_session)
    resp = client.get(f"/admin/templates/{tmpl.id}/anchor", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert 'id="anchor-overlay"' in resp.text
    assert "__anchorInitial" in resp.text


def test_anchor_editor_pre_pop_from_v1_rect(client, db_session):
    """GET with v1 rect anchor derives the 4 corner points and injects them as JSON."""
    # Seed a v1-style anchor: {"x":0.2,"y":0.25,"w":0.6,"h":0.5}
    v1_json = json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5})
    tmpl = _seed_template(db_session, anchor_json=v1_json)

    resp = client.get(f"/admin/templates/{tmpl.id}/anchor", headers=VALID_HEADERS)
    assert resp.status_code == 200

    # Expected corners: TL, TR, BR, BL (x+w=0.8, y+h=0.75)
    # Jinja tojson emits compact JSON: [[0.2, 0.25], [0.8, 0.25], [0.8, 0.75], [0.2, 0.75]]
    for fragment in ["0.2", "0.25", "0.8", "0.75"]:
        assert fragment in resp.text

    # Parse the script tag to verify exact point structure
    import re
    match = re.search(r"window\.__anchorInitial\s*=\s*(\[\[.*?\]\])", resp.text, re.DOTALL)
    assert match, "Could not find __anchorInitial assignment in page"
    parsed = json.loads(match.group(1))
    assert parsed == [[0.2, 0.25], [0.8, 0.25], [0.8, 0.75], [0.2, 0.75]]


def test_anchor_editor_pre_pop_from_v2_quad(client, db_session):
    """GET with v2 quad anchor echoes the stored points directly."""
    quad_points = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    v2_json = json.dumps({
        "version": 2,
        "zones": [{"name": "main", "kind": "quad", "points": quad_points}],
    })
    tmpl = _seed_template(db_session, anchor_json=v2_json)

    resp = client.get(f"/admin/templates/{tmpl.id}/anchor", headers=VALID_HEADERS)
    assert resp.status_code == 200

    import re
    match = re.search(r"window\.__anchorInitial\s*=\s*(\[\[.*?\]\])", resp.text, re.DOTALL)
    assert match, "Could not find __anchorInitial assignment in page"
    parsed = json.loads(match.group(1))
    assert parsed == quad_points


def test_anchor_editor_get_404_for_missing_template(client):
    """GET on a non-existent template id returns 404."""
    resp = client.get("/admin/templates/9999/anchor", headers=VALID_HEADERS)
    assert resp.status_code == 404


def test_anchor_editor_requires_auth(client, db_session):
    """GET and POST without X-Admin-Token header return 401."""
    tmpl = _seed_template(db_session)
    get_resp = client.get(f"/admin/templates/{tmpl.id}/anchor")
    assert get_resp.status_code == 401

    post_resp = client.post(
        f"/admin/templates/{tmpl.id}/anchor",
        json={"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
    )
    assert post_resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /admin/templates/{id}/anchor
# ---------------------------------------------------------------------------

def test_anchor_editor_save_writes_v2_json(client, db_session):
    """POST valid quad points → 200 {ok: true}; DB stores correct v2 JSON."""
    tmpl = _seed_template(db_session)
    quad_points = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]

    with patch(
        "app.routes.templates_admin.composite_service.invalidate_composites_for_template",
        side_effect=_fake_invalidate,
    ):
        resp = client.post(
            f"/admin/templates/{tmpl.id}/anchor",
            headers=VALID_HEADERS,
            json={"points": quad_points},
        )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Reload from DB and verify the stored v2 structure.
    db_session.expire(tmpl)
    db_session.refresh(tmpl)
    saved = json.loads(tmpl.composite_anchor_json)
    assert saved["version"] == 2
    assert len(saved["zones"]) == 1
    zone = saved["zones"][0]
    assert zone["name"] == "main"
    assert zone["kind"] == "quad"
    assert zone["points"] == quad_points


def test_anchor_editor_save_rejects_3_points(client, db_session):
    """POST with only 3 points fails Pydantic validation → 422."""
    tmpl = _seed_template(db_session)
    resp = client.post(
        f"/admin/templates/{tmpl.id}/anchor",
        headers=VALID_HEADERS,
        json={"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]},
    )
    assert resp.status_code == 422


def test_anchor_editor_save_rejects_out_of_bounds(client, db_session):
    """POST with a point outside [0,1] fails Pydantic validation → 422."""
    tmpl = _seed_template(db_session)
    resp = client.post(
        f"/admin/templates/{tmpl.id}/anchor",
        headers=VALID_HEADERS,
        json={"points": [[1.5, 0.5], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
    )
    assert resp.status_code == 422


def test_anchor_editor_save_404_for_missing_template(client):
    """POST on a non-existent template id returns 404."""
    resp = client.post(
        "/admin/templates/9999/anchor",
        headers=VALID_HEADERS,
        json={"points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]},
    )
    assert resp.status_code == 404
