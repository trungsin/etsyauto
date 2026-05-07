"""Tests for anchor_schema parser — v1 shim + v2 zones[]."""
from __future__ import annotations

import json

from app.services import anchor_schema


def test_v1_rect_anchor_returns_single_main_zone():
    raw = json.dumps({"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5})
    zones = anchor_schema.parse_anchor(raw)
    assert len(zones) == 1
    z = zones[0]
    assert z["name"] == "main"
    assert z["kind"] == "rect"
    assert (z["x"], z["y"], z["w"], z["h"]) == (0.2, 0.25, 0.6, 0.5)


def test_v2_zones_array_returned_as_is():
    raw = json.dumps({
        "version": 2,
        "zones": [
            {"name": "front", "kind": "quad",
             "points": [[0.1, 0.1], [0.9, 0.12], [0.88, 0.9], [0.12, 0.88]]},
            {"name": "back", "kind": "rect",
             "x": 0.25, "y": 0.30, "w": 0.50, "h": 0.40},
        ],
    })
    zones = anchor_schema.parse_anchor(raw)
    assert [z["name"] for z in zones] == ["front", "back"]
    assert zones[0]["kind"] == "quad"
    assert len(zones[0]["points"]) == 4
    assert zones[1]["kind"] == "rect"


def test_malformed_json_returns_empty_list():
    assert anchor_schema.parse_anchor("{not json") == []
    assert anchor_schema.parse_anchor(None) == []
    assert anchor_schema.parse_anchor("") == []
    # Valid JSON but wrong shape
    assert anchor_schema.parse_anchor('"just a string"') == []


def test_v2_with_unknown_kind_skipped():
    raw = json.dumps({
        "version": 2,
        "zones": [
            {"name": "front", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
            {"name": "junk", "kind": "circle", "x": 0, "y": 0, "r": 0.5},
            {"name": "back", "kind": "quad",
             "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        ],
    })
    zones = anchor_schema.parse_anchor(raw)
    assert [z["name"] for z in zones] == ["front", "back"]


def test_v2_quad_with_wrong_point_count_skipped():
    raw = json.dumps({
        "version": 2,
        "zones": [
            {"name": "bad", "kind": "quad",
             "points": [[0, 0], [1, 0], [1, 1]]},  # only 3 points
            {"name": "good", "kind": "quad",
             "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        ],
    })
    zones = anchor_schema.parse_anchor(raw)
    assert [z["name"] for z in zones] == ["good"]


def test_v2_zones_normalized_to_floats():
    raw = json.dumps({
        "version": 2,
        "zones": [{"name": "z", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}],
    })
    z = anchor_schema.parse_anchor(raw)[0]
    assert isinstance(z["x"], float)
    assert isinstance(z["w"], float)


def test_max_zones_cap_enforced():
    """More than MAX_ZONES (4) zones in input → only first 4 kept."""
    zones_input = [
        {"name": f"z{i}", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}
        for i in range(7)
    ]
    raw = json.dumps({"version": 2, "zones": zones_input})
    zones = anchor_schema.parse_anchor(raw)
    assert len(zones) == anchor_schema.MAX_ZONES == 4
    assert [z["name"] for z in zones] == ["z0", "z1", "z2", "z3"]


def test_to_v2_upcasts_v1_in_memory():
    raw_v1 = json.dumps({"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6})
    canonical = anchor_schema.to_v2(raw_v1)
    assert canonical["version"] == 2
    assert len(canonical["zones"]) == 1
    assert canonical["zones"][0]["name"] == "main"
    assert canonical["zones"][0]["kind"] == "rect"
