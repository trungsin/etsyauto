"""Tests for composite_service and /composite/preview API — R2 mocked throughout."""
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

ADMIN_TOKEN = "test-admin-token-composite"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Drop/recreate schema before every test — uses StaticPool so all sessions share state."""
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
    """Provide a session sharing the same StaticPool DB as the test client."""
    session = _TestingSessionLocal()
    yield session
    session.close()


def _make_png_rgba(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGBA", (w, h), (200, 100, 50, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_r2_mock(exists: bool = False, listed_keys: list | None = None) -> MagicMock:
    mock = MagicMock()
    mock.object_exists.return_value = exists
    mock.get_public_url.side_effect = lambda key: f"https://cdn.example.com/{key}"
    mock.upload_image.side_effect = lambda data, key: f"https://cdn.example.com/{key}"
    mock.delete_object.return_value = None
    mock.list_objects.return_value = listed_keys or []
    return mock


def _patch_r2(mock_instance):
    """Patch R2StorageClient at the source module — handles lazy imports in services."""
    return patch("app.clients.r2_storage_client.R2StorageClient", return_value=mock_instance)


def _seed_template(session, anchor=None) -> Template:
    anchor = anchor or {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}
    t = Template(
        name="Test Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/templates/base.png",
        composite_anchor_json=json.dumps(anchor),
        default_price_cents=2500,
        variation_options_json="{}",
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_design(session, source_type: str = "upload") -> Design:
    d = Design(
        name="Logo",
        source_type=source_type,
        file_url="https://cdn.example.com/designs/logo.png",
        width=100,
        height=100,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


# ---------------------------------------------------------------------------
# composite_with_anchor — pure function unit tests (no R2)
# ---------------------------------------------------------------------------

def test_composite_with_anchor_returns_bytes():
    from app.services.image_composite import composite_with_anchor
    base = _make_png_rgba(200, 200)
    design = _make_png_rgba(50, 50)
    result = composite_with_anchor(base, design, {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8})
    assert isinstance(result, bytes)
    assert result[:8] == b"\x89PNG\r\n\x1a\n"


def test_composite_with_anchor_clamps_invalid_anchor():
    from app.services.image_composite import composite_with_anchor
    base = _make_png_rgba(100, 100)
    design = _make_png_rgba(40, 40)
    # Out-of-range anchor values are clamped internally — must not raise
    result = composite_with_anchor(base, design, {"x": -0.5, "y": 1.5, "w": 2.0, "h": 0.5})
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# get_or_create_composite — cache miss (fresh composite)
# ---------------------------------------------------------------------------

def test_composite_cache_miss_creates_and_uploads(db_session):
    from app.services import composite_service

    template = _seed_template(db_session)
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    base_png = _make_png_rgba(200, 200)
    design_png = _make_png_rgba(50, 50)

    def fake_urlopen(url):
        content = base_png if "base" in url else design_png
        return io.BytesIO(content)

    with _patch_r2(r2_mock), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url, cached = composite_service.get_or_create_composite(
            db_session, template.id, design.id
        )

    assert not cached
    assert "composites" in url
    r2_mock.upload_image.assert_called_once()


# ---------------------------------------------------------------------------
# get_or_create_composite — cache hit
# ---------------------------------------------------------------------------

def test_composite_cache_hit_returns_cached_true(db_session):
    from app.services import composite_service

    template = _seed_template(db_session)
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=True)  # object_exists returns True

    with _patch_r2(r2_mock):
        url, cached = composite_service.get_or_create_composite(
            db_session, template.id, design.id
        )

    assert cached is True
    assert "composites" in url
    r2_mock.upload_image.assert_not_called()  # no re-upload on cache hit


# ---------------------------------------------------------------------------
# reference_only design rejected
# ---------------------------------------------------------------------------

def test_reference_only_design_rejected(db_session):
    from app.services import composite_service

    template = _seed_template(db_session)
    design = _seed_design(db_session, source_type="reference_only")

    r2_mock = _make_r2_mock()
    with _patch_r2(r2_mock):
        with pytest.raises(ValueError, match="reference_only"):
            composite_service.get_or_create_composite(db_session, template.id, design.id)


# ---------------------------------------------------------------------------
# Template update invalidates cache
# ---------------------------------------------------------------------------

def test_template_update_invalidates_composite_cache(db_session):
    from app.services import template_service

    template = _seed_template(db_session)
    cache_key = f"composites/{template.id}-99.png"

    r2_mock = _make_r2_mock(listed_keys=[cache_key])

    with _patch_r2(r2_mock):
        template_service.update_template(db_session, template.id, name="Updated Name")

    r2_mock.list_objects.assert_called()
    r2_mock.delete_object.assert_called_with(cache_key)


# ---------------------------------------------------------------------------
# Design delete cascades composite cache invalidation
# ---------------------------------------------------------------------------

def test_design_delete_cascades_cache_invalidation(db_session):
    from app.services import design_service

    design = _seed_design(db_session)
    cache_key = f"composites/5-{design.id}.png"

    r2_mock = _make_r2_mock(listed_keys=[cache_key])

    with _patch_r2(r2_mock):
        design_service.delete_design(db_session, design.id)

    r2_mock.delete_object.assert_any_call(cache_key)


# ---------------------------------------------------------------------------
# /composite/preview API endpoint
# ---------------------------------------------------------------------------

def test_composite_preview_endpoint_happy_path(client, db_session):
    template = _seed_template(db_session)
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    base_png = _make_png_rgba(200, 200)
    design_png = _make_png_rgba(50, 50)

    def fake_urlopen(url):
        content = base_png if "base" in url else design_png
        return io.BytesIO(content)

    with _patch_r2(r2_mock), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": template.id, "design_id": design.id},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "composite_url" in body
    assert body["cached"] is False


def test_composite_preview_endpoint_cache_hit(client, db_session):
    template = _seed_template(db_session)
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=True)  # cache hit

    with _patch_r2(r2_mock):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": template.id, "design_id": design.id},
        )

    assert resp.status_code == 200
    assert resp.json()["cached"] is True


def test_composite_preview_missing_template(client):
    r2_mock = _make_r2_mock()
    with _patch_r2(r2_mock):
        resp = client.post(
            "/composite/preview",
            headers=VALID_HEADERS,
            json={"template_id": 99999, "design_id": 99999},
        )
    assert resp.status_code == 400


def test_composite_preview_requires_token(client):
    resp = client.post(
        "/composite/preview",
        json={"template_id": 1, "design_id": 1},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Phase 2 (sub-feature C): per-color composite rendering
# ---------------------------------------------------------------------------

def _seed_apparel_template_with_color_bases(session, colors=("White", "Black")) -> Template:
    """Helper: template with color_base_images_json populated for each color."""
    color_bases = {c: f"https://cdn.example.com/templates/{c.lower()}.png" for c in colors}
    options = {
        "sizes": [{"name": "S", "price_cents": 1900}],
        "colors": list(colors),
        "primary_color": colors[0],
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


def test_per_color_composite_uses_color_specific_base(db_session):
    """When color is provided, cache key includes color and per-color base URL is downloaded."""
    from app.services import composite_service

    template = _seed_apparel_template_with_color_bases(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    downloaded_urls: list[str] = []

    def fake_urlopen(url):
        downloaded_urls.append(url)
        return io.BytesIO(_make_png_rgba(100, 100))

    with _patch_r2(r2_mock), patch("urllib.request.urlopen", side_effect=fake_urlopen):
        url, cached = composite_service.get_or_create_composite(
            db_session, template.id, design.id, color="Black",
        )

    assert not cached
    # Cache key must include color token
    expected_suffix = f"-{design.id}-Black.png"
    assert url.endswith(expected_suffix), f"got {url}"
    # Per-color base URL was downloaded (not the default base)
    assert any("black" in u for u in downloaded_urls), f"downloads={downloaded_urls}"


def test_per_color_composite_color_none_keeps_v0_2_0_key(db_session):
    """color=None falls back to template.base_image_url and cache key has no color suffix."""
    from app.services import composite_service

    template = _seed_apparel_template_with_color_bases(db_session)
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock), patch("urllib.request.urlopen",
                                   side_effect=lambda url: io.BytesIO(_make_png_rgba(80, 80))):
        url, cached = composite_service.get_or_create_composite(
            db_session, template.id, design.id, color=None,
        )

    assert not cached
    # Old-style key: composites/{tid}-{did}.png
    expected_key = f"composites/{template.id}-{design.id}.png"
    assert url.endswith(expected_key)


def test_per_color_composite_falls_back_to_default_base(db_session):
    """When a color has no specific base, fall back to template.base_image_url
    (single-blank templates work in the multi-color creator without per-color uploads)."""
    from app.services import composite_service

    template = _seed_apparel_template_with_color_bases(db_session, colors=("White",))
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock), patch("urllib.request.urlopen",
                                   side_effect=lambda url: io.BytesIO(_make_png_rgba(80, 80))):
        url, cached = composite_service.get_or_create_composite(
            db_session, template.id, design.id, color="Black",
        )
    assert url is not None
    # Cache key still includes the color so per-color caching works
    assert "-Black.png" in url


def test_per_color_composite_raises_when_no_base_at_all(db_session):
    """If template has neither a color base nor a default base_image_url, raise."""
    from app.services import composite_service
    from app.models.template import Template

    t = Template(
        name="No Bases",
        category="apparel",
        base_image_url="",  # explicitly empty
        composite_anchor_json=json.dumps({"x": 0, "y": 0, "w": 1, "h": 1}),
        default_price_cents=1900,
        variation_options_json=json.dumps({"sizes": ["S"], "colors": ["White"]}),
        color_base_images_json=json.dumps({}),
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)

    design = _seed_design(db_session)
    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock):
        with pytest.raises(ValueError, match="neither a color base"):
            composite_service.get_or_create_composite(
                db_session, t.id, design.id, color="Black",
            )


def test_preview_all_colors_renders_each_color(db_session):
    """get_or_create_composites_all_colors returns one entry per color."""
    from app.services import composite_service

    template = _seed_apparel_template_with_color_bases(
        db_session, colors=("White", "Black", "Sand"),
    )
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock), patch("urllib.request.urlopen",
                                   side_effect=lambda url: io.BytesIO(_make_png_rgba(80, 80))):
        results = composite_service.get_or_create_composites_all_colors(
            db_session, template.id, design.id,
        )

    assert len(results) == 3
    colors_returned = {r["color"] for r in results}
    assert colors_returned == {"White", "Black", "Sand"}
    for r in results:
        assert r["error"] is None
        assert r["composite_url"] is not None
        assert f"-{design.id}-" in r["composite_url"]


def test_preview_all_colors_uses_default_base_when_color_missing(db_session):
    """When a color advertised in variation_options has no per-color base, fall
    back to the template default — both colors should render successfully."""
    from app.services import composite_service

    # Only White has a per-color base; Black falls back to template.base_image_url
    template = _seed_apparel_template_with_color_bases(db_session, colors=("White",))
    opts = json.loads(template.variation_options_json)
    opts["colors"] = ["White", "Black"]
    template.variation_options_json = json.dumps(opts)
    db_session.commit()

    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock), patch("urllib.request.urlopen",
                                   side_effect=lambda url: io.BytesIO(_make_png_rgba(80, 80))):
        results = composite_service.get_or_create_composites_all_colors(
            db_session, template.id, design.id,
        )

    by_color = {r["color"]: r for r in results}
    assert by_color["White"]["error"] is None
    assert by_color["Black"]["error"] is None
    assert by_color["Black"]["composite_url"] is not None


def test_design_delete_invalidates_per_color_composites(db_session):
    """Per-color cache keys (composites/{tid}-{did}-Color.png) are also deleted on design delete."""
    from app.services import design_service

    design = _seed_design(db_session)
    keys = [
        f"composites/5-{design.id}.png",
        f"composites/5-{design.id}-White.png",
        f"composites/5-{design.id}-Black.png",
    ]

    r2_mock = _make_r2_mock(listed_keys=keys)
    with _patch_r2(r2_mock):
        design_service.delete_design(db_session, design.id)

    deleted_calls = [c.args[0] for c in r2_mock.delete_object.call_args_list]
    for k in keys:
        assert k in deleted_calls, f"{k} not deleted; got {deleted_calls}"


def test_preview_all_colors_endpoint(client, db_session):
    """End-to-end: POST /composite/preview-all-colors returns N results."""
    from app.services import composite_service  # noqa: F401

    template = _seed_apparel_template_with_color_bases(db_session, colors=("White", "Black"))
    design = _seed_design(db_session)

    r2_mock = _make_r2_mock(exists=False)
    with _patch_r2(r2_mock), patch("urllib.request.urlopen",
                                   side_effect=lambda url: io.BytesIO(_make_png_rgba(80, 80))):
        resp = client.post(
            "/composite/preview-all-colors",
            headers=VALID_HEADERS,
            json={"template_id": template.id, "design_id": design.id},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "results" in body
    assert len(body["results"]) == 2
