"""Tests for Ideas Admin UI routes.

Covers:
- GET /admin/ideas → 401 without token, 200 with valid token
- GET /admin/ideas → filter by keyword_id, status, source
- GET /admin/ideas?sort=velocity → ideas ordered by velocity desc
- GET /admin/ideas/{id} → 200 renders idea detail
- GET /admin/ideas/{id} → 404 on unknown id
- POST /admin/ideas/{id}/reject → sets status=rejected, redirects 303
- POST /admin/ideas/{id}/reject → 404 on unknown id
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.idea import Idea  # noqa: F401
from app.models.idea_signal import IdeaSignal  # noqa: F401
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.services import idea_service, keyword_service

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-ideas-ui"
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
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    s = _TestingSessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_idea(db_session, *, source="etsy_api", slid="L001", keyword_id=None,
               title="Test Idea", status="new") -> Idea:
    idea, _ = idea_service.upsert_idea(
        db_session,
        source=source,
        source_listing_id=slid,
        keyword_id=keyword_id,
        title=title,
        status=status,
    )
    return idea


def _add_signal(db_session, idea_id: int, num_favorers: int, ts_offset_hours: float = 0):
    """Insert a signal row directly to control timestamps."""
    ts = datetime(2024, 1, 1, 12, 0, 0) + timedelta(hours=ts_offset_hours)
    sig = IdeaSignal(idea_id=idea_id, num_favorers=num_favorers, ts=ts)
    db_session.add(sig)
    db_session.commit()
    return sig


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_ideas_requires_auth(client):
    resp = client.get("/admin/ideas")
    assert resp.status_code == 401


def test_list_ideas_with_token(client):
    resp = client.get("/admin/ideas", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Ideas" in resp.text


# ---------------------------------------------------------------------------
# GET /admin/ideas — filtering
# ---------------------------------------------------------------------------

def test_list_ideas_filter_by_status(client, db):
    _seed_idea(db, slid="A1", title="Active Idea", status="new")
    _seed_idea(db, slid="A2", title="Rejected Idea", status="rejected")

    resp = client.get("/admin/ideas?status=rejected", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Rejected Idea" in resp.text
    assert "Active Idea" not in resp.text


def test_list_ideas_filter_by_source(client, db):
    _seed_idea(db, slid="B1", source="etsy_api", title="Etsy Idea")
    _seed_idea(db, slid="B2", source="extension_passive", title="Extension Idea")

    resp = client.get("/admin/ideas?source=extension_passive", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Extension Idea" in resp.text
    assert "Etsy Idea" not in resp.text


def test_list_ideas_filter_by_keyword_id(client, db):
    kw = keyword_service.create_keyword(db, "scented candle")
    _seed_idea(db, slid="C1", title="Candle Idea", keyword_id=kw.id)
    _seed_idea(db, slid="C2", title="Other Idea", keyword_id=None)

    resp = client.get(f"/admin/ideas?keyword_id={kw.id}", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Candle Idea" in resp.text
    assert "Other Idea" not in resp.text


# ---------------------------------------------------------------------------
# GET /admin/ideas?sort=velocity — velocity ordering
# ---------------------------------------------------------------------------

def test_list_ideas_sort_velocity_ordering(client, db):
    """Idea with higher favorer growth rate should appear first."""
    slow = _seed_idea(db, slid="V1", title="Slow Idea")
    fast = _seed_idea(db, slid="V2", title="Fast Idea")

    # slow idea: 0 → 10 favorers over 10 hours = 1 fav/hr
    _add_signal(db, slow.id, num_favorers=0, ts_offset_hours=0)
    _add_signal(db, slow.id, num_favorers=10, ts_offset_hours=10)

    # fast idea: 0 → 100 favorers over 10 hours = 10 fav/hr
    _add_signal(db, fast.id, num_favorers=0, ts_offset_hours=0)
    _add_signal(db, fast.id, num_favorers=100, ts_offset_hours=10)

    resp = client.get("/admin/ideas?sort=velocity", headers=VALID_HEADERS)
    assert resp.status_code == 200
    body = resp.text
    assert body.index("Fast Idea") < body.index("Slow Idea")


# ---------------------------------------------------------------------------
# GET /admin/ideas/{id} — detail page
# ---------------------------------------------------------------------------

def test_idea_detail_200(client, db):
    idea = _seed_idea(db, slid="D1", title="Detail Idea")
    resp = client.get(f"/admin/ideas/{idea.id}", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Detail Idea" in resp.text


def test_idea_detail_shows_signals(client, db):
    idea = _seed_idea(db, slid="D2", title="Signal Idea")
    _add_signal(db, idea.id, num_favorers=42)

    resp = client.get(f"/admin/ideas/{idea.id}", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "42" in resp.text


def test_idea_detail_404_on_unknown(client):
    resp = client.get("/admin/ideas/99999", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/ideas/{id}/reject
# ---------------------------------------------------------------------------

def test_reject_idea_sets_status(client, db):
    idea = _seed_idea(db, slid="R1", title="Reject Me", status="new")
    resp = client.post(
        f"/admin/ideas/{idea.id}/reject",
        headers=VALID_HEADERS,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/ideas"

    db.refresh(idea)
    assert idea.status == "rejected"


def test_reject_idea_404_on_unknown(client):
    resp = client.post("/admin/ideas/99999/reject", headers=VALID_HEADERS)
    assert resp.status_code == 404
