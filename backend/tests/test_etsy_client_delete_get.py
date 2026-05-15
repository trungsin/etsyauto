"""Tests for EtsyApiClient.delete_listing and get_listing (v0.10 additions)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.etsy_api_client import EtsyApiClient
from app.database import Base


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def db():
    s = _TestingSessionLocal()
    yield s
    s.close()


def _make_http_response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = ""
    resp.headers = {}
    return resp


def test_delete_listing_dry_run_returns_none(db, monkeypatch):
    """delete_listing in dry-run mode dispatches happy fixture and returns None."""
    from app import config
    monkeypatch.setattr(config.settings, "etsy_dry_run", True)
    monkeypatch.setattr(config.settings, "etsy_dry_run_scenario", "happy")

    with EtsyApiClient(db) as client:
        result = client.delete_listing(shop_id=42, listing_id=9001)

    assert result is None


def test_delete_listing_treats_404_as_success(db, monkeypatch):
    """If Etsy returns 404 (listing already gone), delete_listing must NOT raise —
    treats as idempotent success."""
    from app import config
    monkeypatch.setattr(config.settings, "etsy_dry_run", False)

    # 404 response that the real client would convert to HTTPStatusError
    not_found_resp = httpx.Response(
        404,
        json={"error": "not found"},
        request=httpx.Request("DELETE", "/shops/1/listings/9001"),
    )
    err = httpx.HTTPStatusError("404 Not Found", request=not_found_resp.request, response=not_found_resp)

    with EtsyApiClient(db) as client:
        with patch.object(client, "_request", side_effect=err):
            # Must not raise
            result = client.delete_listing(shop_id=1, listing_id=9001)

    assert result is None


def test_get_listing_dry_run_returns_payload_with_required_keys(db, monkeypatch):
    """get_listing returns dict with title, description, tags, state — fields the
    sync route relies on."""
    from app import config
    monkeypatch.setattr(config.settings, "etsy_dry_run", True)
    monkeypatch.setattr(config.settings, "etsy_dry_run_scenario", "happy")

    with EtsyApiClient(db) as client:
        data = client.get_listing(9001)

    assert isinstance(data, dict)
    for key in ("title", "description", "tags", "state"):
        assert key in data, f"missing key {key!r} in get_listing payload"
    assert isinstance(data["tags"], list)
