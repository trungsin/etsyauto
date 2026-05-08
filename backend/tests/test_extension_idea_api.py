"""Tests for POST /extension/idea — passive listing log endpoint.

Covers:
- 401 without X-Admin-Token header
- 201 + idea row created on first POST (source=extension_passive)
- 200 + same idea_id returned on duplicate POST (idempotent)
- 422 on missing required field (title)
- IdeaSignal appended when num_favorers provided
- IdeaSignal NOT appended when num_favorers and views_all_time both null
- status NOT overwritten on update (user-review decisions preserved)
"""
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

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-ext-token-abc"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

_BASE_PAYLOAD = {
    "source_listing_id": "99887766",
    "title": "Handmade Ceramic Mug",
    "tags": ["mug", "ceramic", "handmade"],
    "description": "A lovely hand-thrown ceramic mug.",
    "materials": ["clay", "glaze"],
    "reference_image_url": "https://i.etsystatic.com/il_fullxfull.test.jpg",
    "price_cents": 2500,
}


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
    session = _TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_requires_auth_token(client):
    """Missing X-Admin-Token → 401."""
    resp = client.post("/extension/idea", json=_BASE_PAYLOAD)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path — create
# ---------------------------------------------------------------------------

def test_create_idea_returns_201(client, db):
    """First POST creates a new idea, returns 201 with idea_id and created=True."""
    resp = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is True
    assert isinstance(body["idea_id"], int)
    assert body["status"] == "new"

    # Verify DB row
    idea = db.get(Idea, body["idea_id"])
    assert idea is not None
    assert idea.source == "extension_passive"
    assert idea.source_listing_id == "99887766"
    assert idea.title == "Handmade Ceramic Mug"


# ---------------------------------------------------------------------------
# Idempotency — duplicate POST returns 200 with same idea_id
# ---------------------------------------------------------------------------

def test_duplicate_post_returns_200_same_id(client):
    """Second POST with same source_listing_id → 200, same idea_id, created=False."""
    first = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert first.status_code == 201
    idea_id = first.json()["idea_id"]

    second = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["created"] is False
    assert second_body["idea_id"] == idea_id


# ---------------------------------------------------------------------------
# Validation — 422 on missing required field
# ---------------------------------------------------------------------------

def test_missing_title_returns_422(client):
    """Omitting required 'title' field → 422 Unprocessable Entity."""
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "title"}
    resp = client.post("/extension/idea", json=payload, headers=VALID_HEADERS)
    assert resp.status_code == 422


def test_missing_source_listing_id_returns_422(client):
    """Omitting required 'source_listing_id' field → 422."""
    payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "source_listing_id"}
    resp = client.post("/extension/idea", json=payload, headers=VALID_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Signal appended when engagement metrics provided
# ---------------------------------------------------------------------------

def test_signal_appended_when_favorers_provided(client, db):
    """When num_favorers is supplied, an IdeaSignal row is inserted."""
    payload = {**_BASE_PAYLOAD, "num_favorers": 42, "views_all_time": 1200}
    resp = client.post("/extension/idea", json=payload, headers=VALID_HEADERS)
    assert resp.status_code == 201
    idea_id = resp.json()["idea_id"]

    from sqlalchemy import select
    signals = db.scalars(
        select(IdeaSignal).where(IdeaSignal.idea_id == idea_id)
    ).all()
    assert len(signals) == 1
    assert signals[0].num_favorers == 42
    assert signals[0].views_all_time == 1200


def test_no_signal_when_metrics_absent(client, db):
    """When num_favorers and views_all_time both absent, no IdeaSignal is created."""
    resp = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert resp.status_code == 201
    idea_id = resp.json()["idea_id"]

    from sqlalchemy import select
    signals = db.scalars(
        select(IdeaSignal).where(IdeaSignal.idea_id == idea_id)
    ).all()
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# Status preservation — user reject decisions survive re-visits
# ---------------------------------------------------------------------------

def test_status_not_overwritten_on_update(client, db):
    """After a user rejects an idea, re-visiting the listing must not flip status back."""
    # Create idea
    resp = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert resp.status_code == 201
    idea_id = resp.json()["idea_id"]

    # Simulate user rejecting via DB (as admin UI would do)
    idea = db.get(Idea, idea_id)
    idea.status = "rejected"
    db.commit()

    # Second extension POST (simulated re-visit)
    resp2 = client.post("/extension/idea", json=_BASE_PAYLOAD, headers=VALID_HEADERS)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "rejected"

    db.refresh(idea)
    assert idea.status == "rejected"
