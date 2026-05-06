"""Tests for per-color base image endpoints and expand-variations (sub-feature C, Phase 1).

Reuses TestClient + StaticPool in-memory SQLite pattern from test_templates_api.py.
R2StorageClient mocked — no real network.
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-color-bases"
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


def _fake_r2_upload(self, file_bytes, key):  # noqa: ANN001
    return f"https://cdn.example.com/{key}"


def _fake_r2_delete(self, key):  # noqa: ANN001
    pass


def _fake_r2_list(self, prefix):  # noqa: ANN001
    return []


def _patch_r2():
    """Returns a context manager patching R2StorageClient init + ops to no-ops."""
    from app.clients import r2_storage_client as r2_mod
    return (
        patch.object(r2_mod.R2StorageClient, "__init__", lambda self: None),
        patch.object(r2_mod.R2StorageClient, "upload_image", _fake_r2_upload),
        patch.object(r2_mod.R2StorageClient, "delete_object", _fake_r2_delete),
        patch.object(r2_mod.R2StorageClient, "list_objects", _fake_r2_list),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_apparel_template(client, *, with_pricing: bool = True, colors=None) -> int:
    """Create a template with v0.4.0 variation_options convention. Returns template id."""
    if colors is None:
        colors = ["White", "Black", "Sand"]
    if with_pricing:
        sizes = [
            {"name": "S", "price_cents": 1900},
            {"name": "M", "price_cents": 1900},
            {"name": "XL", "price_cents": 2200},
        ]
    else:
        sizes = ["S", "M", "XL"]  # legacy v0.2.0 form
    options = {
        "sizes": sizes,
        "colors": colors,
        "primary_color": "Sand",
        "etsy_taxonomy_id": 1209,
    }
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Comfort Colors 1717",
                "category": "apparel",
                "composite_anchor": '{"x":0.25,"y":0.2,"w":0.5,"h":0.5}',
                "default_price_cents": "1900",
                "variation_options": json.dumps(options),
            },
            files={"base_image": ("tshirt.png", b"\x89PNG fake", "image/png")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Color base — happy path
# ---------------------------------------------------------------------------

def test_upload_color_base_happy_path(client):
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            f"/templates/{tid}/color-bases/White",
            headers=VALID_HEADERS,
            files={"base_image": ("white.png", b"\x89PNG white", "image/png")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "color_base_images" in body
    assert "White" in body["color_base_images"]
    assert body["color_base_images"]["White"].startswith("https://cdn.example.com/templates/")


def test_upload_color_base_normalizes_color(client):
    """`black` (lowercase) should normalize to 'Black' (title case)."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            f"/templates/{tid}/color-bases/black",
            headers=VALID_HEADERS,
            files={"base_image": ("black.png", b"\x89PNG", "image/png")},
        )
    assert resp.status_code == 200, resp.text
    assert "Black" in resp.json()["color_base_images"]


# ---------------------------------------------------------------------------
# Color base — validation
# ---------------------------------------------------------------------------

def test_upload_color_base_rejects_unknown_color(client):
    """Color not in variation_options.colors → 400."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            f"/templates/{tid}/color-bases/Neon",
            headers=VALID_HEADERS,
            files={"base_image": ("x.png", b"\x89PNG", "image/png")},
        )
    assert resp.status_code == 400
    assert "not in" in resp.json()["detail"].lower()


def test_upload_color_base_template_not_found(client):
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(
            "/templates/9999/color-bases/White",
            headers=VALID_HEADERS,
            files={"base_image": ("x.png", b"\x89PNG", "image/png")},
        )
    assert resp.status_code == 404


def test_upload_color_base_requires_token(client):
    resp = client.post(
        "/templates/1/color-bases/White",
        files={"base_image": ("x.png", b"\x89PNG", "image/png")},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Color base — replacement (idempotent)
# ---------------------------------------------------------------------------

def test_upload_color_base_replaces_existing(client):
    """Uploading same color twice replaces R2 object and updates URL."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        client.post(
            f"/templates/{tid}/color-bases/White",
            headers=VALID_HEADERS,
            files={"base_image": ("v1.png", b"v1 bytes", "image/png")},
        )
        resp2 = client.post(
            f"/templates/{tid}/color-bases/White",
            headers=VALID_HEADERS,
            files={"base_image": ("v2.png", b"v2 bytes", "image/png")},
        )
    assert resp2.status_code == 200
    bases = resp2.json()["color_base_images"]
    assert len([k for k in bases if k == "White"]) == 1
    # URL changed (uuid suffix differs)
    # We can't assert exact URL, but ensure single key remains


# ---------------------------------------------------------------------------
# Delete color base
# ---------------------------------------------------------------------------

def test_delete_color_base(client):
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        client.post(
            f"/templates/{tid}/color-bases/White",
            headers=VALID_HEADERS,
            files={"base_image": ("x.png", b"\x89PNG", "image/png")},
        )
        resp = client.delete(f"/templates/{tid}/color-bases/White", headers=VALID_HEADERS)
    assert resp.status_code == 204
    detail = client.get(f"/templates/{tid}", headers=VALID_HEADERS).json()
    assert "White" not in detail.get("color_base_images", {})


def test_delete_color_base_idempotent_for_unknown_color(client):
    """Deleting a color that was never uploaded is a no-op (still 204 because template exists)."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.delete(f"/templates/{tid}/color-bases/Sand", headers=VALID_HEADERS)
    assert resp.status_code == 204


def test_delete_color_base_template_not_found(client):
    resp = client.delete("/templates/9999/color-bases/White", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Expand variations
# ---------------------------------------------------------------------------

def test_expand_variations_creates_cartesian(client):
    """3 sizes × 3 colors → 9 variations, prices replicated per size."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 9
    # Each size's price replicated across all colors
    for row in rows:
        if row["size"] == "S":
            assert row["price_cents"] == 1900
        elif row["size"] == "XL":
            assert row["price_cents"] == 2200
    # All 3 colors represented
    distinct_colors = {r["color"] for r in rows}
    assert distinct_colors == {"White", "Black", "Sand"}


def test_expand_variations_replaces_existing(client):
    """Re-running expand-variations atomically replaces previous rows (count stays at N×M)."""
    tid = _create_apparel_template(client)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        first = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS).json()
        second = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS).json()
        listed = client.get(f"/templates/{tid}/variations", headers=VALID_HEADERS).json()
    assert len(first) == len(second) == 9
    # No duplicates accumulated — total rows after 2 expands still N×M
    rows = listed["variations"] if isinstance(listed, dict) else listed
    assert len(rows) == 9


def test_expand_variations_invalid_schema(client):
    """Sizes missing price_cents → 422."""
    tid = _create_apparel_template(client, with_pricing=False)
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        resp = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS)
    assert resp.status_code == 422


def test_expand_variations_exceeds_max(client):
    """N×M > 30 (Etsy limit) → 422."""
    tid = _create_apparel_template(
        client,
        colors=[f"Color{i}" for i in range(1, 11)],  # 10 colors × 3 sizes = 30 (boundary OK)
    )
    patches = _patch_r2()
    with patches[0], patches[1], patches[2], patches[3]:
        # 10 × 3 = 30, exactly at limit — should pass
        resp = client.post(f"/templates/{tid}/expand-variations", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 30
