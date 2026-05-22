"""Perspective-warp artwork onto a template base using OpenCV quad transform."""
from PIL import Image
import cv2
import numpy as np

from .blend_modes import blend


Point = tuple[float, float]


def normalize_quad(points: list[Point]) -> list[Point]:
    if len(points) != 4:
        raise ValueError("quad must contain exactly 4 points")
    pts = np.array(points, dtype=np.float32)
    center = pts.mean(axis=0)
    ordered = sorted(pts.tolist(), key=lambda p: np.arctan2(p[1] - center[1], p[0] - center[0]))
    sums = [p[0] + p[1] for p in ordered]
    start = sums.index(min(sums))
    ordered = ordered[start:] + ordered[:start]
    return [(float(x), float(y)) for x, y in ordered]


def warp_artwork(artwork: Image.Image, quad: list[Point], canvas_size: tuple[int, int]) -> Image.Image:
    rgba = artwork.convert("RGBA")
    w, h = rgba.size
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    dst = np.array(normalize_quad(quad), dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        np.array(rgba),
        matrix,
        canvas_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    return Image.fromarray(warped, "RGBA")


def _apply_mask(warped: Image.Image, mask: Image.Image | None) -> Image.Image:
    if mask is None:
        return warped
    alpha = np.array(warped.getchannel("A"), dtype=np.float32)
    mask_alpha = np.array(mask.convert("L").resize(warped.size), dtype=np.float32) / 255.0
    merged = np.clip(alpha * mask_alpha, 0, 255).astype(np.uint8)
    result = warped.copy()
    result.putalpha(Image.fromarray(merged, "L"))
    return result


def composite(template_base: Image.Image, artwork: Image.Image, meta: dict) -> Image.Image:
    template = template_base.convert("RGBA")
    warped = warp_artwork(artwork, meta["quad"], template.size)
    warped = _apply_mask(warped, meta.get("mask_image"))
    blended = blend(template, warped, meta.get("blend", "normal"))
    output_size = tuple(meta.get("output_size", template.size))
    if blended.size != output_size:
        blended = blended.resize(output_size, Image.Resampling.LANCZOS)
    return blended.convert("RGB")
