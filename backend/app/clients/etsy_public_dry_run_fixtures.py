"""Etsy public API dry-run fixtures — canned search + detail responses for ETSY_DRY_RUN mode.

Three scenarios for the EtsyPublicClient (x-api-key only, no OAuth):
  - ``happy``      — search returns 5 realistic listings; get_listing returns matching detail
  - ``empty``      — search returns 0 results (keyword with no matches)
  - ``rate_limit`` — search raises 429-equivalent HTTPStatusError

Fixture shapes pinned against Etsy Open API v3 docs (2026-05-07).
"""
from __future__ import annotations

from typing import Any, Callable

import httpx


# ---------------------------------------------------------------------------
# Search result stubs (summary level — from GET /listings/active)
# ---------------------------------------------------------------------------

SEARCH_RESULTS: list[dict] = [
    {
        "listing_id": 9001,
        "title": "Vintage Botanical Print — Set of 3 Wall Art Prints",
        "description": "Beautiful botanical art prints for your home.",
        "tags": ["botanical", "wall art", "vintage", "print", "nature"],
        "taxonomy_id": 68887410,
        "num_favorers": 342,
        "views": 4100,
        "price": {"amount": 1499, "divisor": 100, "currency_code": "USD"},
    },
    {
        "listing_id": 9002,
        "title": "Pressed Flower Art — Wildflower Herbarium Print",
        "description": "Dried wildflower botanical illustration.",
        "tags": ["pressed flower", "herbarium", "botanical", "wildflower"],
        "taxonomy_id": 68887410,
        "num_favorers": 178,
        "views": 2300,
        "price": {"amount": 999, "divisor": 100, "currency_code": "USD"},
    },
    {
        "listing_id": 9003,
        "title": "Custom Watercolor Pet Portrait — Hand Painted",
        "description": "Commission a watercolor portrait of your beloved pet.",
        "tags": ["pet portrait", "watercolor", "custom", "dog", "cat"],
        "taxonomy_id": 66765265,
        "num_favorers": 521,
        "views": 7850,
        "price": {"amount": 4500, "divisor": 100, "currency_code": "USD"},
    },
    {
        "listing_id": 9004,
        "title": "Minimalist Line Art Print — Abstract Face",
        "description": "Modern minimalist line drawing art print.",
        "tags": ["line art", "minimalist", "abstract", "face", "modern"],
        "taxonomy_id": 68887410,
        "num_favorers": 89,
        "views": 1200,
        "price": {"amount": 799, "divisor": 100, "currency_code": "USD"},
    },
    {
        "listing_id": 9005,
        "title": "Celestial Moon Phase Print — Lunar Cycle Wall Art",
        "description": "Beautiful moon phase chart art print.",
        "tags": ["moon", "celestial", "lunar", "astrology", "space"],
        "taxonomy_id": 68887410,
        "num_favorers": 267,
        "views": 3400,
        "price": {"amount": 1299, "divisor": 100, "currency_code": "USD"},
    },
]

# ---------------------------------------------------------------------------
# Detail stubs (full level — from GET /listings/{id}?includes=Images)
# ---------------------------------------------------------------------------

LISTING_DETAILS: dict[str, dict] = {
    "9001": {
        "listing_id": 9001,
        "title": "Vintage Botanical Print — Set of 3 Wall Art Prints",
        "description": "Beautiful botanical art prints for your home. Printed on premium matte paper.",
        "tags": ["botanical", "wall art", "vintage", "print", "nature", "home decor", "gift"],
        "materials": ["archival paper", "archival ink"],
        "taxonomy_id": 68887410,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "num_favorers": 342,
        "views_all_time": 4100,
        "price": {"amount": 1499, "divisor": 100, "currency_code": "USD"},
        "images": [{"url_fullxfull": "https://i.etsystatic.com/dry-run/9001.jpg"}],
    },
    "9002": {
        "listing_id": 9002,
        "title": "Pressed Flower Art — Wildflower Herbarium Print",
        "description": "Dried wildflower botanical illustration printed on natural parchment.",
        "tags": ["pressed flower", "herbarium", "botanical", "wildflower", "nature"],
        "materials": ["parchment paper", "botanical dye"],
        "taxonomy_id": 68887410,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "num_favorers": 178,
        "views_all_time": 2300,
        "price": {"amount": 999, "divisor": 100, "currency_code": "USD"},
        "images": [{"url_fullxfull": "https://i.etsystatic.com/dry-run/9002.jpg"}],
    },
    "9003": {
        "listing_id": 9003,
        "title": "Custom Watercolor Pet Portrait — Hand Painted",
        "description": "Commission a watercolor portrait of your beloved pet. Ships in 2 weeks.",
        "tags": ["pet portrait", "watercolor", "custom", "dog", "cat", "commission"],
        "materials": ["watercolor paper", "watercolor paint"],
        "taxonomy_id": 66765265,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "num_favorers": 521,
        "views_all_time": 7850,
        "price": {"amount": 4500, "divisor": 100, "currency_code": "USD"},
        "images": [{"url_fullxfull": "https://i.etsystatic.com/dry-run/9003.jpg"}],
    },
    "9004": {
        "listing_id": 9004,
        "title": "Minimalist Line Art Print — Abstract Face",
        "description": "Modern minimalist line drawing art print. Printed on 300gsm cardstock.",
        "tags": ["line art", "minimalist", "abstract", "face", "modern", "contemporary"],
        "materials": ["cardstock", "archival ink"],
        "taxonomy_id": 68887410,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "num_favorers": 89,
        "views_all_time": 1200,
        "price": {"amount": 799, "divisor": 100, "currency_code": "USD"},
        "images": [{"url_fullxfull": "https://i.etsystatic.com/dry-run/9004.jpg"}],
    },
    "9005": {
        "listing_id": 9005,
        "title": "Celestial Moon Phase Print — Lunar Cycle Wall Art",
        "description": "Beautiful moon phase chart art print. Perfect for bedroom or living room.",
        "tags": ["moon", "celestial", "lunar", "astrology", "space", "stars"],
        "materials": ["matte paper", "UV-resistant ink"],
        "taxonomy_id": 68887410,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "num_favorers": 267,
        "views_all_time": 3400,
        "price": {"amount": 1299, "divisor": 100, "currency_code": "USD"},
        "images": [{"url_fullxfull": "https://i.etsystatic.com/dry-run/9005.jpg"}],
    },
}


# ---------------------------------------------------------------------------
# Scenario handlers
# ---------------------------------------------------------------------------


def _happy_search(_kw: dict) -> dict:
    return {"results": SEARCH_RESULTS, "count": len(SEARCH_RESULTS)}


def _happy_get_listing(kw: dict) -> dict:
    listing_id = str(kw.get("listing_id", "9001"))
    detail = LISTING_DETAILS.get(listing_id)
    if detail is None:
        # Fall back to first fixture for unknown IDs (test convenience)
        return LISTING_DETAILS["9001"]
    return detail


def _empty_search(_kw: dict) -> dict:
    return {"results": [], "count": 0}


def _empty_get_listing(_kw: dict) -> dict:
    return LISTING_DETAILS["9001"]


def _rate_limit_search(_kw: dict) -> dict:
    request = httpx.Request("GET", "/listings/active")
    response = httpx.Response(429, json={"error": "rate_limited"}, request=request)
    raise httpx.HTTPStatusError(
        "Dry-run scenario: rate_limit", request=request, response=response
    )


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

_SCENARIOS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "happy": {
        "search_active_listings": _happy_search,
        "get_listing": _happy_get_listing,
    },
    "empty": {
        "search_active_listings": _empty_search,
        "get_listing": _empty_get_listing,
    },
    "rate_limit": {
        "search_active_listings": _rate_limit_search,
        "get_listing": _happy_get_listing,
    },
}


def dispatch(scenario: str, method: str, kwargs: dict | None = None) -> Any:
    """Look up a fixture for (scenario, method) and invoke it.

    Falls back to the ``happy`` handler if the scenario doesn't override the method.
    Raises RuntimeError if neither has a handler.
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
    """Return the list of public-API scenario names."""
    return list(_SCENARIOS.keys())
