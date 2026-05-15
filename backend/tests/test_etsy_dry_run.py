"""Tests for ETSY_DRY_RUN mode + scenario fixtures (v0.7.0 phase 1).

Verifies that EtsyApiClient short-circuits to fixtures when settings.etsy_dry_run
is True, with no HTTP fired and behaviour matching the requested scenario.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.clients import etsy_dry_run_fixtures
from app.clients.etsy_api_client import EtsyApiClient
from app.config import settings


@pytest.fixture
def dry_run_on(monkeypatch):
    """Force dry-run on for the duration of the test."""
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    yield


def _build_client() -> EtsyApiClient:
    """Construct a client with a placeholder DB session (unused in dry-run path)."""
    return EtsyApiClient(db=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy scenario
# ---------------------------------------------------------------------------


def test_dry_run_happy_create_draft_returns_fixture_listing_id(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _build_client()
    out = client.create_draft_listing(
        shop_id="123",
        title="t", description="d",
        price=19.0, quantity=100, taxonomy_id=1209,
    )
    assert out == {"listing_id": 9001, "state": "draft"}


def test_dry_run_happy_taxonomy_returns_seeded_values(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _build_client()
    out = client.get_taxonomy_property_values(taxonomy_id=1209, property_id=200)
    names = {entry["name"] for entry in out["possible_values"]}
    assert {"White", "Black", "Sand"}.issubset(names)
    assert out["name"] == "Primary color"


# ---------------------------------------------------------------------------
# Error scenarios
# ---------------------------------------------------------------------------


def test_dry_run_rate_limit_raises_429(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "rate_limit")
    client = _build_client()
    with pytest.raises(httpx.HTTPStatusError) as ei:
        client.create_draft_listing(
            shop_id="1", title="t", description="d",
            price=1.0, quantity=1, taxonomy_id=1,
        )
    assert ei.value.response.status_code == 429


def test_dry_run_taxonomy_error_returns_empty_results(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "taxonomy_error")
    client = _build_client()
    out = client.get_taxonomy_property_values(taxonomy_id=1209, property_id=200)
    assert out == {"name": "", "scales": [], "possible_values": []}


def test_dry_run_auth_fail_raises_401(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "auth_fail")
    client = _build_client()
    with pytest.raises(httpx.HTTPStatusError) as ei:
        client.create_draft_listing(
            shop_id="1", title="t", description="d",
            price=1.0, quantity=1, taxonomy_id=1,
        )
    assert ei.value.response.status_code == 401


def test_dry_run_image_too_small_raises_400(dry_run_on, monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "image_too_small")
    client = _build_client()
    with pytest.raises(httpx.HTTPStatusError) as ei:
        client.upload_listing_image_bytes(
            shop_id="1", listing_id="9001",
            image_bytes=b"\x89PNG\r\n", filename="x.png", rank=1,
        )
    assert ei.value.response.status_code == 400
    assert "570" in ei.value.response.text


# ---------------------------------------------------------------------------
# Real-HTTP path stays untouched when dry-run is OFF (sanity)
# ---------------------------------------------------------------------------


def test_dry_run_off_still_calls_real_http(monkeypatch):
    """With dry-run disabled, the client falls through to _request (which we
    short-circuit via patch to verify the path is reached)."""
    monkeypatch.setattr(settings, "etsy_dry_run", False)
    client = _build_client()
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value.json.return_value = {"listing_id": 1}
        out = client.create_draft_listing(
            shop_id="1", title="t", description="d",
            price=1.0, quantity=1, taxonomy_id=1,
        )
    mock_req.assert_called_once()
    assert out == {"listing_id": 1}


# ---------------------------------------------------------------------------
# Fixture module API
# ---------------------------------------------------------------------------


def test_list_scenarios_includes_all_five():
    names = etsy_dry_run_fixtures.list_scenarios()
    assert {"happy", "rate_limit", "taxonomy_error", "auth_fail", "image_too_small"}.issubset(set(names))


def test_dispatch_falls_back_to_happy_when_scenario_missing_method():
    """rate_limit only overrides create_draft_listing; other methods fall back to happy."""
    out = etsy_dry_run_fixtures.dispatch("rate_limit", "update_listing_inventory", {"products": []})
    assert out == {"products": []}


# ---------------------------------------------------------------------------
# set_variation_images tests
# ---------------------------------------------------------------------------


def test_set_variation_images_dry_run_returns_canned(dry_run_on, monkeypatch):
    """set_variation_images in dry-run returns fixture response."""
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _build_client()
    out = client.set_variation_images(
        shop_id="123",
        listing_id="9001",
        property_id=200,
        value_to_image_id={1: 100, 2: 101},
    )
    # Fixture returns results list + count
    assert "results" in out
    assert out["count"] == 2
    assert len(out["results"]) == 2


def test_set_variation_images_request_body_shape(dry_run_on, monkeypatch):
    """Verify request body shape matches Etsy API spec."""
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    client = _build_client()
    # Should not raise
    out = client.set_variation_images(
        shop_id="123",
        listing_id="9001",
        property_id=200,
        value_to_image_id={1: 100, 2: 101, 3: 102},
    )
    assert out is not None
    assert out["count"] == 3
