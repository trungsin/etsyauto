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
