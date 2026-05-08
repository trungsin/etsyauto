"""Tests for Keywords Admin UI routes.

Covers:
- GET /admin/keywords → 401 without token, 200 with valid token
- GET /admin/keywords → renders keyword list (empty + with rows)
- POST /admin/keywords → creates keyword, redirects 303
- POST /admin/keywords → duplicate term returns 400 with error message
- POST /admin/keywords → empty term returns 400
- POST /admin/keywords/{id}/toggle → flips enabled, returns HTML row partial
- POST /admin/keywords/{id}/run → dispatches background thread, redirects 303
- POST /admin/keywords/{id}/delete → removes keyword, redirects 303
"""
from __future__ import annotations

from unittest.mock import patch

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
from app.services import keyword_service

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-kw-ui"
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
def db_session():
    s = _TestingSessionLocal()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_keywords_requires_auth(client):
    resp = client.get("/admin/keywords")
    assert resp.status_code == 401


def test_list_keywords_with_token(client):
    resp = client.get("/admin/keywords", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Keywords" in resp.text


# ---------------------------------------------------------------------------
# GET — list renders rows
# ---------------------------------------------------------------------------

def test_list_keywords_shows_rows(client, db_session):
    keyword_service.create_keyword(db_session, "vintage poster")
    resp = client.get("/admin/keywords", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "vintage poster" in resp.text


# ---------------------------------------------------------------------------
# POST — create
# ---------------------------------------------------------------------------

def test_create_keyword_redirects(client):
    resp = client.post(
        "/admin/keywords",
        data={"term": "boho wall art"},
        headers=VALID_HEADERS,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/keywords"


def test_create_keyword_persists(client, db_session):
    client.post("/admin/keywords", data={"term": "cat gifts"}, headers=VALID_HEADERS)
    keywords = keyword_service.list_keywords(db_session)
    assert any(k.term == "cat gifts" for k in keywords)


def test_create_keyword_duplicate_returns_400(client, db_session):
    keyword_service.create_keyword(db_session, "duplicate term")
    resp = client.post(
        "/admin/keywords",
        data={"term": "duplicate term"},
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 400
    assert "already exists" in resp.text


def test_create_keyword_empty_term_returns_400(client):
    resp = client.post(
        "/admin/keywords",
        data={"term": "   "},
        headers=VALID_HEADERS,
    )
    assert resp.status_code == 400
    assert "empty" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /{id}/toggle — HTMX partial
# ---------------------------------------------------------------------------

def test_toggle_keyword_flips_enabled(client, db_session):
    kw = keyword_service.create_keyword(db_session, "toggle me")
    assert kw.enabled is True

    resp = client.post(f"/admin/keywords/{kw.id}/toggle", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Disabled" in resp.text  # row partial reflects new state

    db_session.refresh(kw)
    assert kw.enabled is False


def test_toggle_keyword_404_on_missing(client):
    resp = client.post("/admin/keywords/9999/toggle", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/run — manual mining dispatched via background thread
# ---------------------------------------------------------------------------

def test_run_keyword_dispatches_thread_and_redirects(client, db_session):
    kw = keyword_service.create_keyword(db_session, "run me now")

    # Patch threading.Thread to avoid actually spawning a thread in tests
    with patch("app.routes.keywords_admin.threading.Thread") as mock_thread_cls:
        mock_thread = mock_thread_cls.return_value
        resp = client.post(
            f"/admin/keywords/{kw.id}/run",
            headers=VALID_HEADERS,
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert "/admin/keywords" in resp.headers["location"]
    mock_thread_cls.assert_called_once()
    mock_thread.start.assert_called_once()


def test_run_keyword_404_on_missing(client):
    resp = client.post("/admin/keywords/9999/run", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /{id}/delete
# ---------------------------------------------------------------------------

def test_delete_keyword_removes_row(client, db_session):
    kw = keyword_service.create_keyword(db_session, "delete me")
    resp = client.post(
        f"/admin/keywords/{kw.id}/delete",
        headers=VALID_HEADERS,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    remaining = keyword_service.list_keywords(db_session)
    assert all(k.id != kw.id for k in remaining)


def test_delete_keyword_404_on_missing(client):
    resp = client.post("/admin/keywords/9999/delete", headers=VALID_HEADERS)
    assert resp.status_code == 404
