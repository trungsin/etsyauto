"""Tests for /templates/{id}/variations JSON API endpoints.

Uses FastAPI TestClient with in-memory SQLite. R2StorageClient is mocked.
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
from app.models.design import Design  # noqa: F401 — registers table
from app.models.template import Template  # noqa: F401 — registers table
from app.models.template_variation import TemplateVariation  # noqa: F401 — registers table

# ---------------------------------------------------------------------------
# DB / client fixtures
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-variations-token-xyz"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _Session()
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


# ---------------------------------------------------------------------------
# Helper: create a template in DB directly
# ---------------------------------------------------------------------------

def _create_template(client: TestClient) -> int:
    """Create a template via API (mocking R2) and return its id."""
    with patch(
        "app.clients.r2_storage_client.R2StorageClient.__init__", lambda self: None
    ), patch(
        "app.clients.r2_storage_client.R2StorageClient.upload_image",
        lambda self, b, k: f"https://cdn.example.com/{k}",
    ):
        resp = client.post(
            "/templates",
            headers=VALID_HEADERS,
            data={
                "name": "Test Shirt",
                "category": "apparel",
                "composite_anchor": '{"x":0.2,"y":0.2,"w":0.6,"h":0.6}',
                "default_price_cents": "1500",
                "variation_options": json.dumps({"sizes": ["S", "M", "L"], "colors": ["white", "black"]}),
            },
            files={"base_image": ("shirt.png", b"\x89PNG", "image/png")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_variations(count: int) -> list[dict]:
    """Generate `count` unique variations cycling through sizes/colors."""
    sizes = [f"SIZE{i}" for i in range(count)]
    return [{"size": s, "color": "white", "price_cents": 1000 + i * 10, "sku": f"SKU-{i}"} for i, s in enumerate(sizes)]


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_bulk_replace_requires_token(client):
    tid = _create_template(client)
    resp = client.post(f"/templates/{tid}/variations", json={"variations": []})
    assert resp.status_code == 401


def test_list_variations_requires_token(client):
    tid = _create_template(client)
    resp = client.get(f"/templates/{tid}/variations")
    assert resp.status_code == 401


def test_clear_variations_requires_token(client):
    tid = _create_template(client)
    resp = client.delete(f"/templates/{tid}/variations")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Bulk replace — happy path
# ---------------------------------------------------------------------------

def test_bulk_replace_30_variations(client):
    """POST 30 variations must succeed and return all 30."""
    tid = _create_template(client)
    variations = _make_variations(30)
    resp = client.post(
        f"/templates/{tid}/variations",
        headers=VALID_HEADERS,
        json={"variations": variations},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["variations"]) == 30


def test_bulk_replace_31_variations_returns_400(client):
    """POST 31 variations must return 400."""
    tid = _create_template(client)
    variations = _make_variations(31)
    resp = client.post(
        f"/templates/{tid}/variations",
        headers=VALID_HEADERS,
        json={"variations": variations},
    )
    assert resp.status_code == 400


def test_bulk_replace_clears_old_and_inserts_new(client):
    """Second POST replaces first — old rows gone, count matches new batch."""
    tid = _create_template(client)

    # First batch: 5 rows
    first = _make_variations(5)
    resp1 = client.post(f"/templates/{tid}/variations", headers=VALID_HEADERS, json={"variations": first})
    assert resp1.status_code == 200
    assert len(resp1.json()["variations"]) == 5

    # Second batch: 3 different rows — should replace
    second = [{"size": "XL", "color": "red", "price_cents": 2000, "sku": None},
              {"size": "XXL", "color": "red", "price_cents": 2100, "sku": None},
              {"size": "3XL", "color": "red", "price_cents": 2200, "sku": None}]
    resp2 = client.post(f"/templates/{tid}/variations", headers=VALID_HEADERS, json={"variations": second})
    assert resp2.status_code == 200

    # GET must return 3, not 5+3
    resp3 = client.get(f"/templates/{tid}/variations", headers=VALID_HEADERS)
    assert resp3.status_code == 200
    assert len(resp3.json()["variations"]) == 3


# ---------------------------------------------------------------------------
# Unique constraint
# ---------------------------------------------------------------------------

def test_unique_constraint_blocks_duplicate_size_color(client):
    """Duplicate (size, color) in same batch must return 409."""
    tid = _create_template(client)
    dup = [
        {"size": "S", "color": "white", "price_cents": 1000},
        {"size": "S", "color": "white", "price_cents": 1100},  # duplicate
    ]
    resp = client.post(f"/templates/{tid}/variations", headers=VALID_HEADERS, json={"variations": dup})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 404 for unknown template
# ---------------------------------------------------------------------------

def test_bulk_replace_404_unknown_template(client):
    resp = client.post(
        "/templates/99999/variations",
        headers=VALID_HEADERS,
        json={"variations": []},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE clear
# ---------------------------------------------------------------------------

def test_clear_variations(client):
    tid = _create_template(client)
    # seed 3 rows
    client.post(
        f"/templates/{tid}/variations",
        headers=VALID_HEADERS,
        json={"variations": _make_variations(3)},
    )
    # clear
    resp = client.delete(f"/templates/{tid}/variations", headers=VALID_HEADERS)
    assert resp.status_code == 204

    # list should be empty
    resp2 = client.get(f"/templates/{tid}/variations", headers=VALID_HEADERS)
    assert resp2.json()["variations"] == []


# ---------------------------------------------------------------------------
# PUT single price update
# ---------------------------------------------------------------------------

def test_update_variation_price(client):
    tid = _create_template(client)
    rows = client.post(
        f"/templates/{tid}/variations",
        headers=VALID_HEADERS,
        json={"variations": [{"size": "M", "color": "blue", "price_cents": 1000}]},
    ).json()["variations"]
    vid = rows[0]["id"]

    resp = client.put(
        f"/templates/{tid}/variations/{vid}",
        headers=VALID_HEADERS,
        json={"price_cents": 2500},
    )
    assert resp.status_code == 200
    assert resp.json()["price_cents"] == 2500
