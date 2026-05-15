"""Tests for Phase 7: admin template detail page + image pool HTML partial.

Routes covered:
  GET  /admin/templates/{id}               → renders templates/detail.html
  GET  /admin/templates/{id}/images/partial → renders _image_pool.html partial

Auth model: admin_token cookie promoted to X-Admin-Token by middleware.
External services (R2, composite cache) not exercised here.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.template import Template
from app.models.template_image import TemplateImage

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "pool-ui-test-token"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    from app.models.template import Template  # noqa: F401
    from app.models.template_variation import TemplateVariation  # noqa: F401
    from app.models.design import Design  # noqa: F401
    from app.models.template_image import TemplateImage  # noqa: F401
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

def _seed_template(session, *, with_colors: bool = False, with_base_image: bool = True) -> Template:
    opts = {"sizes": ["S", "M"], "colors": ["White", "Black"]} if with_colors else {}
    t = Template(
        name="Pool Test Tee",
        category="apparel",
        base_image_url="https://cdn.example.com/base.png" if with_base_image else None,
        composite_anchor_json=json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}),
        default_price_cents=2500,
        variation_options_json=json.dumps(opts),
        color_base_images_json=json.dumps({}),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_image(session, template_id: int, rank: int = 0, color: str | None = None) -> TemplateImage:
    img = TemplateImage(
        template_id=template_id,
        image_url=f"https://cdn.example.com/img{rank}.png",
        color=color,
        rank=rank,
        anchor_json="{}",
        role="mockup",
    )
    session.add(img)
    session.commit()
    session.refresh(img)
    return img


# ---------------------------------------------------------------------------
# GET /admin/templates/{id} — detail page
# ---------------------------------------------------------------------------

class TestTemplateDetailPage:
    def test_renders_ok(self, client, db_session):
        tmpl = _seed_template(db_session)
        resp = client.get(f"/admin/templates/{tmpl.id}", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "Pool Test Tee" in resp.text
        assert "image-pool" in resp.text

    def test_requires_auth(self, client, db_session):
        tmpl = _seed_template(db_session)
        resp = client.get(f"/admin/templates/{tmpl.id}")
        assert resp.status_code == 401

    def test_404_for_missing(self, client):
        resp = client.get("/admin/templates/9999", headers=VALID_HEADERS)
        assert resp.status_code == 404

    def test_shows_image_pool_section(self, client, db_session):
        tmpl = _seed_template(db_session, with_colors=True)
        _seed_image(db_session, tmpl.id, rank=0, color="White")
        resp = client.get(f"/admin/templates/{tmpl.id}", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "Image Pool" in resp.text
        # Thumbnail for the image
        assert "https://cdn.example.com/img0.png" in resp.text

    def test_shows_migrate_button_when_no_real_rows_but_base_image(self, client, db_session):
        tmpl = _seed_template(db_session, with_base_image=True)
        # No TemplateImage rows seeded — pool is empty, legacy fields exist
        resp = client.get(f"/admin/templates/{tmpl.id}", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "Migrate legacy" in resp.text

    def test_hides_migrate_button_when_real_rows_exist(self, client, db_session):
        tmpl = _seed_template(db_session, with_base_image=True)
        _seed_image(db_session, tmpl.id, rank=0)
        resp = client.get(f"/admin/templates/{tmpl.id}", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "Migrate legacy" not in resp.text

    def test_colors_passed_to_context(self, client, db_session):
        tmpl = _seed_template(db_session, with_colors=True)
        resp = client.get(f"/admin/templates/{tmpl.id}", headers=VALID_HEADERS)
        assert resp.status_code == 200
        # Color options rendered in the pool upload drop-zone
        assert "White" in resp.text
        assert "Black" in resp.text

    def test_cookie_auth_accepted(self, client, db_session):
        """admin_token cookie is promoted to X-Admin-Token by middleware."""
        tmpl = _seed_template(db_session)
        resp = client.get(
            f"/admin/templates/{tmpl.id}",
            cookies={"admin_token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /admin/templates/{id}/images/partial — HTMX partial
# ---------------------------------------------------------------------------

class TestImagePoolPartial:
    def test_renders_partial(self, client, db_session):
        tmpl = _seed_template(db_session, with_colors=True)
        _seed_image(db_session, tmpl.id, rank=0, color="White")
        resp = client.get(f"/admin/templates/{tmpl.id}/images/partial", headers=VALID_HEADERS)
        assert resp.status_code == 200
        # Partial renders the pool table header and a row
        assert "Image Pool" in resp.text
        assert "img0.png" in resp.text

    def test_requires_auth(self, client, db_session):
        tmpl = _seed_template(db_session)
        resp = client.get(f"/admin/templates/{tmpl.id}/images/partial")
        assert resp.status_code == 401

    def test_404_for_missing(self, client):
        resp = client.get("/admin/templates/9999/images/partial", headers=VALID_HEADERS)
        assert resp.status_code == 404

    def test_empty_pool_shows_drop_zone(self, client, db_session):
        tmpl = _seed_template(db_session)
        resp = client.get(f"/admin/templates/{tmpl.id}/images/partial", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "drop-zone" in resp.text

    def test_virtual_rows_rendered(self, client, db_session):
        """When no real rows exist, virtual rows from base_image_url are shown."""
        tmpl = _seed_template(db_session, with_base_image=True)
        resp = client.get(f"/admin/templates/{tmpl.id}/images/partial", headers=VALID_HEADERS)
        assert resp.status_code == 200
        # base_image_url virtual row should appear
        assert "base.png" in resp.text
        assert "virtual" in resp.text

    def test_multiple_real_rows_all_rendered(self, client, db_session):
        tmpl = _seed_template(db_session, with_colors=True)
        _seed_image(db_session, tmpl.id, rank=0, color="White")
        _seed_image(db_session, tmpl.id, rank=1, color="Black")
        resp = client.get(f"/admin/templates/{tmpl.id}/images/partial", headers=VALID_HEADERS)
        assert resp.status_code == 200
        assert "img0.png" in resp.text
        assert "img1.png" in resp.text
        # Count shows 2/20
        assert "2 / 20" in resp.text
