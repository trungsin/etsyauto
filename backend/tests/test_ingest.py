"""Tests for POST /ingest endpoint.

Uses FastAPI TestClient with an in-memory SQLite DB so no real DB is touched.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.listing import Listing  # noqa: F401 — ensures table is registered


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"

_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    Base.metadata.create_all(bind=_engine)
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Drop and recreate all tables before each test for isolation."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestHappyPath:
    def test_returns_200_with_job_id(self, client):
        payload = {
            "listing_id": 999,
            "source_url": "https://www.etsy.com/listing/999/test-product",
            "title": "Test Listing Title",
            "images": ["https://i.etsystatic.com/123/img.jpg"],
        }
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] > 0
        assert data["listing_id"] == 999
        assert data["status"] == "new"

    def test_idempotent_returns_existing_when_status_new(self, client):
        payload = {
            "listing_id": 123,
            "source_url": "https://www.etsy.com/listing/123/item",
            "title": "First",
            "images": [],
        }
        resp1 = client.post("/ingest", json=payload)
        resp2 = client.post("/ingest", json=payload)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["job_id"] == resp2.json()["job_id"]

    def test_reingest_allowed_after_pushed(self, client):
        """If existing listing status is 'pushed', the row is reset to status 'new'.

        The unique constraint on etsy_listing_id means we UPDATE rather than INSERT.
        """
        Base.metadata.create_all(bind=_engine)
        db = _TestingSessionLocal()
        seed_row = Listing(
            etsy_listing_id="456",
            original_title="Pushed Item",
            status="pushed",
        )
        db.add(seed_row)
        db.commit()
        seeded_id = seed_row.id
        db.close()

        payload = {
            "listing_id": 456,
            "source_url": "https://www.etsy.com/listing/456/item",
            "title": "Pushed Item",
            "images": [],
        }
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Same row (same job_id / pk) but status reset to 'new'
        assert data["job_id"] == seeded_id
        assert data["status"] == "new"

        # Verify only one row — no duplicate inserted
        db2 = _TestingSessionLocal()
        count = db2.query(Listing).filter(Listing.etsy_listing_id == "456").count()
        db2.close()
        assert count == 1

    def test_validation_rejects_zero_listing_id(self, client):
        payload = {
            "listing_id": 0,
            "source_url": "https://www.etsy.com/listing/0/item",
        }
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 422

    def test_title_optional(self, client):
        payload = {
            "listing_id": 789,
            "source_url": "https://www.etsy.com/listing/789/item",
        }
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "new"
