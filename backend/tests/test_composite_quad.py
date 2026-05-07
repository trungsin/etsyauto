"""Tests for composite_quad and composite_zones (cv2 perspective warp)."""
from __future__ import annotations

import io
import time

import numpy as np
import pytest
from PIL import Image

from app.services import image_composite


def _solid_png(w: int, h: int, rgba: tuple[int, int, int, int]) -> bytes:
    img = Image.new("RGBA", (w, h), rgba)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(b: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(b)).convert("RGBA")
    return np.asarray(img)


def test_quad_warps_into_specified_corners():
    """A red opaque design warped into the centre quad lands inside it; pixels
    outside the quad keep the white base."""
    base = _solid_png(200, 200, (255, 255, 255, 255))
    design = _solid_png(100, 100, (255, 0, 0, 255))
    points = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]

    out = image_composite.composite_quad(base, design, points)
    arr = _decode(out)
    assert arr.shape == (200, 200, 4)
    # Centre pixel: red
    assert tuple(arr[100, 100, :3]) == (255, 0, 0)
    # Top-left corner pixel: white (outside quad)
    assert tuple(arr[5, 5, :3]) == (255, 255, 255)


def test_quad_preserves_alpha_outside_warp_region():
    """A transparent base remains transparent outside the warp region."""
    transparent = _solid_png(100, 100, (0, 0, 0, 0))
    design = _solid_png(50, 50, (0, 200, 0, 255))
    points = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]

    out = image_composite.composite_quad(transparent, design, points)
    arr = _decode(out)
    # Centre is the green design
    assert arr[50, 50, 1] > 100  # green channel
    assert arr[50, 50, 3] == 255
    # Far corner stays transparent
    assert arr[5, 5, 3] == 0


def test_quad_invalid_point_count_raises():
    base = _solid_png(50, 50, (255, 255, 255, 255))
    design = _solid_png(20, 20, (0, 0, 0, 255))
    with pytest.raises(ValueError, match="4 points"):
        image_composite.composite_quad(base, design, [[0, 0], [1, 0], [1, 1]])


def test_quad_output_under_size_cap():
    """Output PNG honours the 2 MB cap (smoke check on encode helper)."""
    # A modestly sized solid base will encode well under 2 MB; the helper just
    # has to short-circuit rather than re-encode.
    base = _solid_png(800, 800, (255, 255, 255, 255))
    design = _solid_png(400, 400, (200, 50, 50, 255))
    points = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    out = image_composite.composite_quad(base, design, points)
    assert len(out) <= image_composite._MAX_OUTPUT_BYTES


def test_quad_render_perf_gate():
    """1024×1024 base + 800×800 design should warp in under 3 s on CI."""
    base = _solid_png(1024, 1024, (240, 240, 240, 255))
    design = _solid_png(800, 800, (50, 100, 200, 255))
    points = [[0.2, 0.2], [0.8, 0.22], [0.78, 0.78], [0.22, 0.76]]
    t0 = time.perf_counter()
    image_composite.composite_quad(base, design, points)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"composite_quad too slow: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# composite_zones (mixed rect + quad)
# ---------------------------------------------------------------------------

def test_composite_zones_skips_zone_without_design():
    """Zone whose name is missing from designs_by_name → base shows through."""
    base = _solid_png(100, 100, (200, 200, 200, 255))
    design = _solid_png(50, 50, (0, 255, 0, 255))
    zones = [
        {"name": "front", "kind": "rect", "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
        {"name": "back", "kind": "rect", "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5},
    ]
    designs = {"front": design}  # no "back" entry
    out = image_composite.composite_zones(base, zones, designs)
    arr = _decode(out)
    # back-zone area kept the original base color (gray)
    assert tuple(arr[80, 80, :3]) == (200, 200, 200)


def test_composite_zones_layers_in_array_order():
    """Later zones paint over earlier zones when they overlap."""
    base = _solid_png(100, 100, (255, 255, 255, 255))
    red = _solid_png(50, 50, (255, 0, 0, 255))
    green = _solid_png(50, 50, (0, 255, 0, 255))
    full_zone = lambda name: {"name": name, "kind": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    out = image_composite.composite_zones(
        base,
        [full_zone("a"), full_zone("b")],
        {"a": red, "b": green},
    )
    arr = _decode(out)
    # Last-applied (green) wins
    assert tuple(arr[50, 50, :3]) == (0, 255, 0)


def test_composite_zones_mixed_rect_and_quad():
    """Mixed kinds in a single template render together without errors."""
    base = _solid_png(200, 200, (255, 255, 255, 255))
    design_rect = _solid_png(40, 40, (255, 0, 0, 255))
    design_quad = _solid_png(40, 40, (0, 0, 255, 255))
    zones = [
        {"name": "front", "kind": "rect", "x": 0.0, "y": 0.0, "w": 0.4, "h": 0.4},
        {"name": "back", "kind": "quad",
         "points": [[0.5, 0.5], [0.95, 0.5], [0.95, 0.95], [0.5, 0.95]]},
    ]
    out = image_composite.composite_zones(
        base, zones, {"front": design_rect, "back": design_quad},
    )
    arr = _decode(out)
    # rect zone landed red in top-left
    assert tuple(arr[20, 20, :3]) == (255, 0, 0)
    # quad zone landed blue in bottom-right
    assert tuple(arr[150, 150, :3]) == (0, 0, 255)
