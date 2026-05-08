"""Tests for EtsyPublicClient — public Etsy v3 API wrapper (x-api-key, no OAuth).

Covers:
- search_active_listings: dry-run happy returns 5 results
- search_active_listings: dry-run empty returns []
- search_active_listings: dry-run rate_limit raises HTTPStatusError 429
- get_listing: dry-run happy returns detail dict with expected fields
- search_active_listings: real HTTP path parses results from mocked response
- _request: 429 triggers retry then succeeds on retry
- _request: 5xx exhausts retries and raises
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.clients.etsy_public_client import EtsyPublicClient, MAX_RETRIES
from app.config import settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dry_run_on(monkeypatch):
    """Force dry-run on for the duration of the test."""
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    yield


@pytest.fixture
def dry_run_off(monkeypatch):
    """Ensure dry-run is off for the duration of the test."""
    monkeypatch.setattr(settings, "etsy_dry_run", False)
    yield


def _client() -> EtsyPublicClient:
    """Build a client with a dummy API key (not used in dry-run paths)."""
    return EtsyPublicClient(api_key="test-key")


# ---------------------------------------------------------------------------
# Dry-run: search_active_listings
# ---------------------------------------------------------------------------


def test_dry_run_happy_search_returns_five_listings(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _client()
    results = client.search_active_listings("botanical print")
    assert len(results) == 5
    # Verify shape — each result has listing_id and title
    for r in results:
        assert "listing_id" in r
        assert "title" in r


def test_dry_run_empty_search_returns_empty_list(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "empty")
    client = _client()
    results = client.search_active_listings("xyzzy-no-match-1234")
    assert results == []


def test_dry_run_rate_limit_search_raises_429(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "rate_limit")
    client = _client()
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.search_active_listings("botanical print")
    assert exc_info.value.response.status_code == 429


# ---------------------------------------------------------------------------
# Dry-run: get_listing
# ---------------------------------------------------------------------------


def test_dry_run_happy_get_listing_returns_detail(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _client()
    detail = client.get_listing("9001")
    assert detail["listing_id"] == 9001
    assert "tags" in detail
    assert "images" in detail
    assert detail["images"][0]["url_fullxfull"].startswith("https://")


def test_dry_run_get_listing_falls_back_to_first_fixture_for_unknown_id(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _client()
    # ID 9999 not in fixtures — should fall back to 9001 fixture
    detail = client.get_listing("9999")
    assert detail["listing_id"] == 9001


# ---------------------------------------------------------------------------
# Real HTTP path: search parses results correctly
# ---------------------------------------------------------------------------


def test_real_http_search_parses_results(dry_run_off, monkeypatch):
    """With dry-run off, search calls _request and returns parsed results list."""
    client = _client()
    fake_body = {"results": [{"listing_id": 1, "title": "Test"}], "count": 1}

    with patch.object(client, "_request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.json.return_value = fake_body
        mock_req.return_value = mock_resp

        # Patch time.sleep to avoid actual wait
        with patch("app.clients.etsy_public_client.time.sleep"):
            results = client.search_active_listings("test keyword")

    assert results == [{"listing_id": 1, "title": "Test"}]
    mock_req.assert_called_once()
    call_args = mock_req.call_args
    assert call_args[0][0] == "GET"
    assert "/listings/active" in call_args[0][1]


# ---------------------------------------------------------------------------
# Retry logic: 429 then success
# ---------------------------------------------------------------------------


def test_request_retries_on_429_then_succeeds(dry_run_off, monkeypatch):
    """_request retries on 429 and succeeds on the next attempt."""
    client = _client()

    call_count = 0

    def _side_effect(method, path, **kwargs):
        nonlocal call_count
        call_count += 1
        req = httpx.Request(method, f"https://openapi.etsy.com{path}")
        if call_count == 1:
            # First call: 429
            return httpx.Response(429, json={"error": "rate_limited"}, request=req)
        # Second call: 200
        return httpx.Response(200, json={"results": []}, request=req)

    with patch.object(client._http, "request", side_effect=_side_effect):
        with patch("app.clients.etsy_public_client.time.sleep"):
            resp = client._request("GET", "/listings/active")

    assert resp.status_code == 200
    assert call_count == 2


# ---------------------------------------------------------------------------
# Retry logic: 5xx exhausts and raises
# ---------------------------------------------------------------------------


def test_request_raises_after_max_retries_on_5xx(dry_run_off, monkeypatch):
    """_request raises HTTPStatusError after MAX_RETRIES exhausted on 500."""
    client = _client()

    def _always_500(method, path, **kwargs):
        req = httpx.Request(method, f"https://openapi.etsy.com{path}")
        return httpx.Response(500, json={"error": "internal"}, request=req)

    with patch.object(client._http, "request", side_effect=_always_500):
        with patch("app.clients.etsy_public_client.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                client._request("GET", "/listings/active")

    assert exc_info.value.response.status_code == 500
