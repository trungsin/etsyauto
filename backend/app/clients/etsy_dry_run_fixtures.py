"""Etsy dry-run fixtures — canned responses for ETSY_DRY_RUN mode.

Etsy v3 has no public sandbox. To validate the listing-creator pipeline end-to-end
without burning real quota, set ``ETSY_DRY_RUN=true`` in ``backend/.env``. Each
public ``EtsyApiClient`` method then short-circuits to a fixture keyed by
``ETSY_DRY_RUN_SCENARIO``.

Scenarios:
  - ``happy``           — every call succeeds with realistic v3 shapes
  - ``rate_limit``      — first ``create_draft_listing`` raises 429 (real client
                          retries; fixture switches to happy on retry)
  - ``taxonomy_error``  — ``get_taxonomy_property_values`` returns empty results
                          → upstream ``etsy_taxonomy.resolve_property_values``
                          raises ValueError that maps to 422 in the route
  - ``auth_fail``       — first ``create_draft_listing`` raises 401
  - ``image_too_small`` — ``upload_listing_image_bytes`` raises 400 with
                          "image must be at least 570x570"

Fixture response shapes pinned against Etsy Open API v3 docs at brainstorm
authoring time (2026-05-07). If Etsy changes its response format, update here.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx


# ---------------------------------------------------------------------------
# Happy-path responses
# ---------------------------------------------------------------------------


def _happy_create_draft(_kw: dict) -> dict:
    return {"listing_id": 9001, "state": "draft"}


def _happy_inventory(kw: dict) -> dict:
    return {"products": kw.get("products", [])}


def _happy_image_upload(kw: dict) -> dict:
    return {"listing_image_id": 12345, "rank": kw.get("rank", 1)}


def _happy_variation_images(kw: dict) -> dict:
    property_id = kw.get("property_id", 0)
    value_to_image_id: dict = kw.get("value_to_image_id", {})
    return {
        "results": [
            {"property_id": property_id, "value_id": vid, "image_id": img_id}
            for vid, img_id in value_to_image_id.items()
        ],
        "count": len(value_to_image_id),
    }


def _happy_delete_listing(_kw: dict) -> None:
    """delete_listing returns nothing on success (Etsy soft-deletes to inactive)."""
    return None


def _happy_get_listing(kw: dict) -> dict:
    """Sample Etsy listing payload — covers fields needed by sync."""
    return {
        "listing_id": int(kw.get("listing_id", 9001)),
        "state": "active",
        "title": "Sample Etsy Title (from dry-run)",
        "description": "Sample description body.",
        "tags": ["sample", "draft", "tee"],
        "price": {"amount": 1900, "divisor": 100, "currency_code": "USD"},
        "quantity": 100,
        "num_favorers": 0,
        "views": 0,
    }


def _happy_taxonomy(kw: dict) -> dict:
    """Mimic the real client's filtered shape: name + scales + possible_values."""
    pid = int(kw.get("property_id") or 0)
    if pid == 200:
        return {
            "name": "Primary color",
            "scales": [],
            "possible_values": [
                {"value_id": 1, "name": "White", "scale_id": None},
                {"value_id": 2, "name": "Black", "scale_id": None},
                {"value_id": 3, "name": "Sand", "scale_id": None},
            ],
        }
    return {
        "name": "Size",
        "scales": [{"scale_id": 25, "display_name": "US letter"}],
        "possible_values": [
            {"value_id": 100, "name": "S", "scale_id": 25},
            {"value_id": 101, "name": "M", "scale_id": 25},
            {"value_id": 102, "name": "L", "scale_id": 25},
            {"value_id": 103, "name": "XL", "scale_id": 25},
        ],
    }


# ---------------------------------------------------------------------------
# Error scenarios
# ---------------------------------------------------------------------------


def _http_error(status: int, body: dict, method: str = "POST", path: str = "/x") -> Callable:
    """Return a handler that raises httpx.HTTPStatusError matching real client behavior."""

    def _raise(_kw: dict) -> dict:
        request = httpx.Request(method, path)
        response = httpx.Response(status, json=body, request=request)
        raise httpx.HTTPStatusError(
            f"Dry-run scenario error: {status}", request=request, response=response
        )

    return _raise


def _taxonomy_error_lookup(_kw: dict) -> dict:
    """Empty values — caller raises ValueError when no value matches."""
    return {"name": "", "scales": [], "possible_values": []}


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

# Per-scenario method handlers. Missing keys fall back to ``happy``.
_SCENARIOS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "happy": {
        "create_draft_listing": _happy_create_draft,
        "update_listing_inventory": _happy_inventory,
        "upload_listing_image_bytes": _happy_image_upload,
        "upload_listing_image": _happy_image_upload,
        "set_variation_images": _happy_variation_images,
        "get_taxonomy_property_values": _happy_taxonomy,
        "delete_listing": _happy_delete_listing,
        "get_listing": _happy_get_listing,
    },
    "rate_limit": {
        "create_draft_listing": _http_error(429, {"error": "rate_limited"}),
    },
    "taxonomy_error": {
        "get_taxonomy_property_values": _taxonomy_error_lookup,
    },
    "auth_fail": {
        "create_draft_listing": _http_error(401, {"error": "invalid_token"}, method="POST"),
    },
    "image_too_small": {
        "upload_listing_image_bytes": _http_error(
            400, {"error": "image must be at least 570x570"}
        ),
        "upload_listing_image": _http_error(
            400, {"error": "image must be at least 570x570"}
        ),
    },
}


def dispatch(scenario: str, method: str, kwargs: dict | None = None) -> Any:
    """Look up a fixture for ``(scenario, method)`` and invoke it.

    Falls back to the ``happy`` handler if the scenario doesn't override the
    method. Raises ``RuntimeError`` if neither has a handler.
    """
    kwargs = kwargs or {}
    handlers = _SCENARIOS.get(scenario, {})
    fn = handlers.get(method) or _SCENARIOS["happy"].get(method)
    if fn is None:
        raise RuntimeError(
            f"No dry-run fixture for scenario={scenario!r} method={method!r}"
        )
    return fn(kwargs)


def list_scenarios() -> list[str]:
    """Return the list of scenario names — used by docs/health endpoint."""
    return list(_SCENARIOS.keys())
