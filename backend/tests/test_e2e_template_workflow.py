"""E2E integration test: full template workflow — create → variations → design → composite.

Covers the complete admin-token-authenticated path:
  1. Create template (R2 mocked)
  2. Add 6 variations (3 sizes × 2 colors)
  3. Upload design (RGBA PNG)
  4. POST /composite/preview → cached=False
  5. POST /composite/preview again → cached=True
  6. PUT template (update price) → invalidate cache → cached=False
"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401 — register table
from app.models.template import Template  # noqa: F401 — register table
from app.models.template_variation import TemplateVariation  # noqa: F401 — register table

# ---------------------------------------------------------------------------
# Test DB — StaticPool so all connections share the same in-memory instance
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "e2e-admin-token-workflow"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Fresh schema before every test."""
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


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _make_png_rgba(w: int = 200, h: int = 200) -> bytes:
    """Return valid RGBA PNG bytes."""
    img = Image.new("RGBA", (w, h), (180, 80, 40, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# R2 mock factory
# ---------------------------------------------------------------------------

def _make_r2_mock(exists: bool = False) -> MagicMock:
    """Build a reusable R2StorageClient mock."""
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
# Full E2E workflow test
# ---------------------------------------------------------------------------

def test_full_template_workflow(client):
    """End-to-end: create template → 6 variations → upload design → composite preview
    with cache miss, cache hit, and cache invalidation on template update."""

    r2_mock = _make_r2_mock(exists=False)
    base_png = _make_png_rgba(400, 400)
    design_png = _make_png_rgba(100, 100)

    # -- Step 1: Create template --------------------------------------------------
    with _patch_r2(r2_mock):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Classic T-Shirt",
                "category": "apparel",
                "composite_anchor": json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}),
                "default_price_cents": "2500",
                "variation_options": json.dumps({"sizes": ["S", "M", "L"], "colors": ["white", "black"]}),
            },
            files={"base_image": ("tshirt.png", base_png, "image/png")},
        )
    assert resp.status_code == 201, f"Create template failed: {resp.text}"
    template = resp.json()
    template_id = template["id"]
    assert template["name"] == "Classic T-Shirt"
    assert template["category"] == "apparel"
    assert template["default_price_cents"] == 2500

    # -- Step 2: Add 6 variations (3 sizes × 2 colors) ----------------------------
    variations_payload = [
        {"size": "S", "color": "white", "price_cents": 2500, "sku": "TS-S-WHT"},
        {"size": "S", "color": "black", "price_cents": 2500, "sku": "TS-S-BLK"},
        {"size": "M", "color": "white", "price_cents": 2600, "sku": "TS-M-WHT"},
        {"size": "M", "color": "black", "price_cents": 2600, "sku": "TS-M-BLK"},
        {"size": "L", "color": "white", "price_cents": 2700, "sku": "TS-L-WHT"},
        {"size": "L", "color": "black", "price_cents": 2700, "sku": "TS-L-BLK"},
    ]
    resp = client.post(
        f"/templates/{template_id}/variations",
        headers=VALID_HEADERS,
        json={"variations": variations_payload},
    )
    assert resp.status_code == 200, f"Bulk replace variations failed: {resp.text}"
    variations_body = resp.json()
    assert len(variations_body["variations"]) == 6, (
        f"Expected 6 variations, got {len(variations_body['variations'])}"
    )

    # Verify variation structure
    returned_sizes = {v["size"] for v in variations_body["variations"]}
    returned_colors = {v["color"] for v in variations_body["variations"]}
    assert returned_sizes == {"S", "M", "L"}
    assert returned_colors == {"white", "black"}

    # -- Step 3: Upload design (RGBA PNG) -----------------------------------------
    with _patch_r2(r2_mock):
        resp = client.post(
            "/designs",
            headers=VALID_HEADERS,
            data={"name": "Star Logo", "source_type": "upload"},
            files={"file": ("star-logo.png", design_png, "image/png")},
        )
    assert resp.status_code == 201, f"Design upload failed: {resp.text}"
    design = resp.json()
    design_id = design["id"]
    assert design["name"] == "Star Logo"
    assert design["source_type"] == "upload"

    # -- Step 4: Composite preview → cached=False (cache miss) --------------------
    def fake_urlopen_miss(url):
        u = url.full_url if hasattr(url, "full_url") else url
        content = base_png if "templates" in u or "base" in u else design_png
        return io.BytesIO(content)

    r2_mock_miss = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock_miss), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen_miss):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": template_id, "design_id": design_id},
        )
    assert resp.status_code == 200, f"Composite preview (miss) failed: {resp.text}"
    preview_body = resp.json()
    assert "composite_url" in preview_body
    assert preview_body["cached"] is False, "First call must be a cache miss"

    # -- Step 5: Second composite call → cached=True (cache hit) ------------------
    r2_mock_hit = _make_r2_mock(exists=True)
    with _patch_r2(r2_mock_hit):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": template_id, "design_id": design_id},
        )
    assert resp.status_code == 200, f"Composite preview (hit) failed: {resp.text}"
    assert resp.json()["cached"] is True, "Second call must be a cache hit"

    # -- Step 6: PUT template → cache invalidated → cached=False ------------------
    # Mock R2 with a cached composite key to verify deletion
    cache_key = f"composites/{template_id}-{design_id}.png"
    r2_mock_invalidate = _make_r2_mock(exists=False)
    r2_mock_invalidate.list_objects.return_value = [cache_key]

    with _patch_r2(r2_mock_invalidate):
        put_resp = client.put(
            f"/templates/{template_id}",
            headers=VALID_HEADERS,
            json={"default_price_cents": 3000},
        )
    assert put_resp.status_code == 200, f"Template PUT failed: {put_resp.text}"
    assert put_resp.json()["default_price_cents"] == 3000
    # Verify cache was cleared
    r2_mock_invalidate.delete_object.assert_called_with(cache_key)

    # Post-invalidation composite → cache miss (exists=False after deletion)
    r2_mock_after_invalidate = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock_after_invalidate), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen_miss):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": template_id, "design_id": design_id},
        )
    assert resp.status_code == 200, f"Composite after invalidation failed: {resp.text}"
    assert resp.json()["cached"] is False, "After template update, cache must be invalidated"


# ---------------------------------------------------------------------------
# Auth guard — all E2E endpoints require admin token
# ---------------------------------------------------------------------------

def test_e2e_requires_admin_token_on_all_endpoints(client):
    """Each endpoint returns 401 when admin token is absent."""
    endpoints = [
        ("GET", "/templates"),
        ("POST", "/templates"),
        ("GET", "/admin/templates"),
        ("POST", "/composite/preview"),
    ]
    for method, path in endpoints:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} did not return 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Variations max-limit enforcement (Etsy limit = 30)
# ---------------------------------------------------------------------------

def test_e2e_variations_over_limit_rejected(client):
    """31 variations must be rejected (Etsy hard limit = 30)."""
    r2_mock = _make_r2_mock()
    with _patch_r2(r2_mock):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Over-Limit Test",
                "category": "apparel",
                "composite_anchor": json.dumps({"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}),
                "default_price_cents": "1000",
            },
            files={"base_image": ("t.png", _make_png_rgba(), "image/png")},
        )
    assert resp.status_code == 201
    tid = resp.json()["id"]

    over_limit = [
        {"size": f"SIZE{i}", "color": "white", "price_cents": 1000}
        for i in range(31)
    ]
    resp = client.post(
        f"/templates/{tid}/variations",
        headers=VALID_HEADERS,
        json={"variations": over_limit},
    )
    assert resp.status_code == 400, "31 variations must return 400"


# ---------------------------------------------------------------------------
# reference_only design rejected from composite
# ---------------------------------------------------------------------------

def test_e2e_reference_only_design_rejected_from_composite(client):
    """reference_only designs must not be composited — returns 400."""
    r2_mock = _make_r2_mock()
    base_png = _make_png_rgba()
    design_png = _make_png_rgba(50, 50)

    with _patch_r2(r2_mock):
        t_resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Ref Test",
                "category": "print",
                "composite_anchor": json.dumps({"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}),
                "default_price_cents": "0",
            },
            files={"base_image": ("img.png", base_png, "image/png")},
        )
        d_resp = client.post(
            "/designs",
            headers=VALID_HEADERS,
            data={"name": "Ref Design", "source_type": "reference_only"},
            files={"file": ("ref.png", design_png, "image/png")},
        )

    tid = t_resp.json()["id"]
    did = d_resp.json()["id"]

    with _patch_r2(r2_mock):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": tid, "design_id": did},
        )
    assert resp.status_code == 400, "reference_only design must be rejected by composite"
