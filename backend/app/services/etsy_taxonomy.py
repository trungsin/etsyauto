"""Etsy taxonomy & property helpers — maps human-readable names to Etsy value_ids.

Etsy v3 variations require numeric `property_id` + `value_id` pairs that vary by
taxonomy node. This module provides:
  - canonical property IDs for apparel (the only category supported in v0.4.0)
  - `resolve_property_values` — looks up value_id for given names, with in-memory cache

Public Etsy reference: https://developers.etsy.com/documentation/reference#operation/getPropertiesByTaxonomyId
"""
from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded constants (apparel taxonomy — Adult Tops & Tees)
# ---------------------------------------------------------------------------
#
# These reflect Etsy's public taxonomy mapping at knowledge-cutoff Jan 2026.
# verify-against-live should run before going to production: there is a TODO
# in listing_creator_service that calls get_taxonomy_property_values() at
# request time, so an outdated ID surfaces as a clear error during creation.

TAXONOMY_APPAREL_TSHIRT: int = 1209  # Adult Tops & Tees → T-Shirts
PROPERTY_PRIMARY_COLOR: int = 200
PROPERTY_SIZE: int = 506


# ---------------------------------------------------------------------------
# Lookup with cache
# ---------------------------------------------------------------------------

# Module-level cache: (taxonomy_id, property_id) → {name (normalized): value_id}
# Bounded to avoid unbounded growth across many taxonomy/property combinations.
_VALUE_CACHE: dict[tuple[int, int], dict[str, int]] = {}
_CACHE_LIMIT = 64


def _normalize(name: str) -> str:
    """Normalize a property value name for case/whitespace-insensitive matching."""
    return name.strip().lower()


def _fetch_values(client, taxonomy_id: int, property_id: int) -> dict[str, int]:
    """Hit Etsy and convert response to {normalized_name: value_id}."""
    resp = client.get_taxonomy_property_values(taxonomy_id, property_id)
    mapping: dict[str, int] = {}
    for entry in resp.get("results", []) or resp.get("possible_values", []):
        # Etsy returns dict per value: {"value_id": int, "name": str, ...}
        value_id = entry.get("value_id") or entry.get("id")
        name = entry.get("name") or entry.get("value")
        if value_id is None or name is None:
            continue
        mapping[_normalize(name)] = int(value_id)
    return mapping


def resolve_property_values(
    client,
    taxonomy_id: int,
    property_id: int,
    names: list[str],
) -> list[int]:
    """Return Etsy value_id for each name in *names*, in input order.

    Caches the {name: value_id} map per (taxonomy_id, property_id) so subsequent
    calls within the same process reuse the result.

    Args:
        client: An EtsyApiClient instance with `get_taxonomy_property_values`.
        taxonomy_id: Etsy taxonomy node id (e.g. 1209 for adult t-shirts).
        property_id: Etsy property id (e.g. 200 for primary color).
        names: Human-readable names to resolve (e.g. ["White", "Black"]).

    Returns:
        List of value_id ints in same order as *names*.

    Raises:
        ValueError: If any name has no matching value_id in the property.
    """
    cache_key = (taxonomy_id, property_id)
    mapping = _VALUE_CACHE.get(cache_key)
    if mapping is None:
        mapping = _fetch_values(client, taxonomy_id, property_id)
        # LRU-style trim
        if len(_VALUE_CACHE) >= _CACHE_LIMIT:
            _VALUE_CACHE.pop(next(iter(_VALUE_CACHE)))
        _VALUE_CACHE[cache_key] = mapping
        logger.info(
            "Cached %d property values for taxonomy=%d property=%d",
            len(mapping), taxonomy_id, property_id,
        )

    resolved: list[int] = []
    missing: list[str] = []
    for n in names:
        norm = _normalize(n)
        vid = mapping.get(norm)
        if vid is None:
            missing.append(n)
        else:
            resolved.append(vid)

    if missing:
        available = sorted(mapping.keys())[:20]
        raise ValueError(
            f"Etsy taxonomy {taxonomy_id} property {property_id} has no value(s) "
            f"matching {missing}. Available (first 20): {available}"
        )
    return resolved


def clear_cache() -> None:
    """Reset the in-memory value cache (used by tests)."""
    _VALUE_CACHE.clear()
