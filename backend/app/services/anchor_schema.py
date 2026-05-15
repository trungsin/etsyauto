"""Anchor schema parser — converts `composite_anchor_json` strings to a list of zones.

Two on-disk shapes:

  v1 (legacy):  ``{"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}``
                Single rectangular print zone, used by all v0.4.x templates.

  v2 (current): ``{"version": 2, "zones": [{"name": ..., "kind": ..., ...}]}``
                Array of zones, each ``rect`` (Pillow paste) or ``quad``
                (cv2.warpPerspective). Multi-zone composites layer in array order.

`parse_anchor` always returns ``list[Zone]``. v1 inputs are upcast in memory to a
single-element list with a synthetic name ``"main"`` so callers never branch on
schema version. The on-disk JSON stays untouched until the next admin write.
"""
from __future__ import annotations

import json
import logging
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)


class RectZone(TypedDict):
    name: str
    kind: Literal["rect"]
    x: float
    y: float
    w: float
    h: float


class QuadZone(TypedDict):
    name: str
    kind: Literal["quad"]
    points: list[list[float]]  # [[x, y], ...] of length 4, clockwise from top-left


Zone = RectZone | QuadZone


# Maximum zones per template — soft cap for sanity / cache size
MAX_ZONES = 4

# Maximum zones allowed per image in the image pool (stricter cap)
MAX_ZONES_PER_IMAGE = 2


def validate_for_image(raw: str | None) -> list[Zone]:
    """Parse anchor JSON and enforce MAX_ZONES_PER_IMAGE limit.

    Raises:
        ValueError: If zone count exceeds MAX_ZONES_PER_IMAGE.
    """
    zones = parse_anchor(raw)
    if len(zones) > MAX_ZONES_PER_IMAGE:
        raise ValueError(
            f"Image anchor has {len(zones)} zones; maximum is {MAX_ZONES_PER_IMAGE}"
        )
    return zones


def parse_anchor(raw: str | None) -> list[Zone]:
    """Parse a ``composite_anchor_json`` string into a list of normalized zones.

    Returns ``[]`` if the input is empty/malformed; callers treat empty as "no
    zones to render" and raise their own ValueError where appropriate.
    """
    try:
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, dict):
        return []

    # v2 — explicit version key
    if data.get("version") == 2:
        zones_raw = data.get("zones") or []
        if not isinstance(zones_raw, list):
            return []
        zones: list[Zone] = []
        for z in zones_raw[:MAX_ZONES]:
            normalized = _normalize_zone(z)
            if normalized is not None:
                zones.append(normalized)
        return zones

    # v1 — single rect anchor, no version key
    if all(k in data for k in ("x", "y", "w", "h")):
        try:
            return [
                {
                    "name": "main",
                    "kind": "rect",
                    "x": float(data["x"]),
                    "y": float(data["y"]),
                    "w": float(data["w"]),
                    "h": float(data["h"]),
                }
            ]
        except (TypeError, ValueError):
            return []

    return []


def _normalize_zone(z: object) -> Zone | None:
    """Validate and coerce one zone dict; return None on invalid shape."""
    if not isinstance(z, dict):
        return None
    name = str(z.get("name") or "").strip()
    kind = z.get("kind")
    if not name or kind not in ("rect", "quad"):
        return None

    if kind == "rect":
        try:
            return {
                "name": name,
                "kind": "rect",
                "x": float(z["x"]),
                "y": float(z["y"]),
                "w": float(z["w"]),
                "h": float(z["h"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    # quad
    points = z.get("points")
    if not isinstance(points, list) or len(points) != 4:
        return None
    try:
        coords: list[list[float]] = [[float(p[0]), float(p[1])] for p in points]
    except (TypeError, ValueError, IndexError):
        return None
    return {"name": name, "kind": "quad", "points": coords}


def to_v2(raw: str | None) -> dict:
    """Return the anchor as a canonical v2 dict (for serializing back).

    Useful when an admin edits a v1 template and we want the next write to be v2.
    """
    zones = parse_anchor(raw)
    return {"version": 2, "zones": zones}
