"""Etsy taxonomy & property helpers — maps human-readable names to Etsy value_ids.

Etsy v3 inventory variations require numeric `property_id` + `value_id` pairs,
plus the human `property_name` string and (for scaled properties like Size) a
`scale_id` to disambiguate between US/UK/EU/etc. scales.

Public Etsy reference:
  https://developers.etsy.com/documentation/reference#operation/getPropertiesByTaxonomyId
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded constants (apparel taxonomy — Women's Tops & Tees → T-shirts)
# ---------------------------------------------------------------------------
#
# Verified against live Etsy seller-taxonomy on 2026-05-14. Etsy reorganized
# their taxonomy IDs since older docs (1209 no longer exists). Sizes property
# is now a per-taxonomy ID, not a generic 506 ("Length" dimension).

TAXONOMY_APPAREL_TSHIRT: int = 559  # Women's Clothing > Tops & Tees > T-shirts
PROPERTY_PRIMARY_COLOR: int = 200
PROPERTY_SIZE: int = 52047899294  # Women's clothing size (includes XS/S/M/L/XL/2XL/3XL)
# Default scale for Size — Etsy returns multiple value_ids per letter (S/M/L)
# spread across 8 scales (US numeric, US letter, UK, FR, DE, AU, JP, IN letter).
# US letter (scale_id=25) is the canonical POD t-shirt scale.
DEFAULT_SIZE_SCALE_ID: int = 25


# ---------------------------------------------------------------------------
# Lookup with cache — stores the full property metadata (name + scales + values)
# ---------------------------------------------------------------------------

_PROPERTY_CACHE: dict[tuple[int, int], dict] = {}
_CACHE_LIMIT = 64


def _normalize(name: str) -> str:
    return name.strip().lower()


def _fetch_property_meta(client, taxonomy_id: int, property_id: int) -> dict:
    """Fetch + cache property metadata: {name, scales, values:list[dict]}.

    Each value dict carries: ``value_id``, ``name``, ``scale_id`` (or None).
    """
    cache_key = (taxonomy_id, property_id)
    cached = _PROPERTY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    resp = client.get_taxonomy_property_values(taxonomy_id, property_id)
    meta = {
        "name": resp.get("name") or "",
        "scales": resp.get("scales") or [],
        # Etsy may return values under either "possible_values" (real API
        # filtered response) or "results" (legacy / dry-run fixture).
        "values": resp.get("possible_values") or resp.get("results") or [],
    }

    if len(_PROPERTY_CACHE) >= _CACHE_LIMIT:
        _PROPERTY_CACHE.pop(next(iter(_PROPERTY_CACHE)))
    _PROPERTY_CACHE[cache_key] = meta
    logger.info(
        "Cached property meta taxonomy=%d property=%d name=%r values=%d scales=%d",
        taxonomy_id, property_id, meta["name"], len(meta["values"]), len(meta["scales"]),
    )
    return meta


def resolve_property_values(
    client,
    taxonomy_id: int,
    property_id: int,
    names: list[str],
    *,
    preferred_scale_id: int | None = None,
) -> list[int]:
    """Return value_id for each name in *names*, preserving order.

    Back-compat with earlier signature; for scaled properties pass
    ``preferred_scale_id`` to pick the correct scale.
    """
    entries = resolve_property_inventory_entries(
        client, taxonomy_id, property_id, names,
        preferred_scale_id=preferred_scale_id,
    )
    return [e["value_ids"][0] for e in entries]


def resolve_property_inventory_entries(
    client,
    taxonomy_id: int,
    property_id: int,
    names: list[str],
    *,
    preferred_scale_id: int | None = None,
) -> list[dict]:
    """Return Etsy `property_values` entries (one per *names*) ready for
    inventory PUT.

    Each entry shape:
        {
            "property_id": int,
            "property_name": str,
            "value_ids": [int],
            "values": [str],
            "scale_id": int | None,  # only present when not None
        }
    """
    meta = _fetch_property_meta(client, taxonomy_id, property_id)
    if not meta["values"]:
        raise ValueError(
            f"Etsy taxonomy {taxonomy_id} property {property_id} returned no values."
        )

    # Group candidates by normalized name → list of value entries.
    by_name: dict[str, list[dict]] = {}
    for v in meta["values"]:
        n = v.get("name") or v.get("value") or ""
        by_name.setdefault(_normalize(n), []).append(v)

    out: list[dict] = []
    missing: list[str] = []
    for n in names:
        candidates = by_name.get(_normalize(n)) or []
        if not candidates:
            missing.append(n)
            continue
        # Prefer the requested scale, else first candidate.
        pick = next(
            (c for c in candidates if c.get("scale_id") == preferred_scale_id),
            candidates[0],
        )
        value_id = int(pick.get("value_id") or pick.get("id"))
        entry: dict = {
            "property_id": property_id,
            "property_name": meta["name"],
            "value_ids": [value_id],
            "values": [pick.get("name") or pick.get("value") or n],
        }
        scale_id = pick.get("scale_id")
        if scale_id is not None:
            entry["scale_id"] = int(scale_id)
        out.append(entry)

    if missing:
        available = sorted({(v.get("name") or v.get("value") or "") for v in meta["values"]})[:20]
        raise ValueError(
            f"Etsy taxonomy {taxonomy_id} property {property_id} ({meta['name']!r}) "
            f"has no value(s) matching {missing}. Available (first 20): {available}"
        )
    return out


def clear_cache() -> None:
    """Reset the in-memory property cache (used by tests)."""
    _PROPERTY_CACHE.clear()
