"""Image compositing service — alpha-composite product cutout onto generated background.

Also exports `composite_with_anchor` for template-based mockup preview.
"""
import io
import logging
from io import BytesIO

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

_CANVAS_SIZE = 1024  # px — must match Imagen output size
_CUTOUT_SCALE = 0.70  # cutout occupies 70% of canvas width
_BOTTOM_BIAS = 0.55  # vertical center offset: >0.5 shifts product slightly downward
_SHADOW_BLUR_RADIUS = 8  # pixels for drop shadow blur

_MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB composite output limit


def composite_with_anchor(
    base_bytes: bytes,
    design_bytes: bytes,
    anchor: dict,
) -> bytes:
    """Composite a design PNG onto a template base image using normalized anchor coords.

    The design is aspect-ratio fitted within the anchor box, centred inside it.

    Args:
        base_bytes: Template base image bytes (any format, converted to RGBA).
        design_bytes: Design PNG bytes with alpha channel.
        anchor: Dict {x, y, w, h} — all 0-1 floats, relative to base image dimensions.

    Returns:
        PNG bytes of the composited image (≤2 MB, resized if necessary).
    """
    base = Image.open(BytesIO(base_bytes)).convert("RGBA")
    design = Image.open(BytesIO(design_bytes)).convert("RGBA")

    # Clamp anchor values to valid range
    ax = max(0.0, min(1.0, anchor.get("x", 0.0)))
    ay = max(0.0, min(1.0, anchor.get("y", 0.0)))
    aw = max(0.001, min(1.0, anchor.get("w", 1.0)))
    ah = max(0.001, min(1.0, anchor.get("h", 1.0)))

    target_w = int(base.width * aw)
    target_h = int(base.height * ah)

    # Maintain design aspect ratio — fit within target box
    design_aspect = design.width / design.height if design.height > 0 else 1.0
    box_aspect = target_w / target_h if target_h > 0 else 1.0
    if design_aspect > box_aspect:
        new_w = target_w
        new_h = max(1, int(target_w / design_aspect))
    else:
        new_h = target_h
        new_w = max(1, int(target_h * design_aspect))

    design_resized = design.resize((new_w, new_h), Image.LANCZOS)

    # Centre within target box
    pos_x = int(base.width * ax) + (target_w - new_w) // 2
    pos_y = int(base.height * ay) + (target_h - new_h) // 2

    # Composite onto transparent overlay then alpha_composite onto base
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(design_resized, (pos_x, pos_y))
    result = Image.alpha_composite(base, overlay)

    buf = BytesIO()
    result.save(buf, format="PNG", optimize=True)
    output = buf.getvalue()

    # Resize down if output exceeds 2 MB
    if len(output) > _MAX_OUTPUT_BYTES:
        scale = (_MAX_OUTPUT_BYTES / len(output)) ** 0.5
        new_base_w = max(1, int(result.width * scale))
        new_base_h = max(1, int(result.height * scale))
        result = result.resize((new_base_w, new_base_h), Image.LANCZOS)
        buf = BytesIO()
        result.save(buf, format="PNG", optimize=True)
        output = buf.getvalue()

    logger.info(
        "composite_with_anchor: base=%dx%d anchor=(%.2f,%.2f,%.2f,%.2f) "
        "design_placed=%dx%d at (%d,%d) → %d bytes",
        base.width, base.height, ax, ay, aw, ah,
        new_w, new_h, pos_x, pos_y, len(output),
    )
    return output


def composite_cutout_on_background(cutout_png: bytes, background_png: bytes) -> bytes:
    """Alpha-composite a product cutout onto an Imagen-generated background.

    Layout:
    - Background resized to _CANVAS_SIZE x _CANVAS_SIZE
    - Cutout scaled to ~70% of canvas width, centered horizontally,
      positioned slightly below vertical center for natural product placement
    - Subtle drop shadow applied under the cutout via Pillow alpha blur

    Args:
        cutout_png: PNG bytes of the product with transparent background (RGBA).
        background_png: PNG bytes of the background scene (RGB or RGBA).

    Returns:
        PNG bytes of the composited image.
    """
    canvas_px = _CANVAS_SIZE

    # --- load images ---
    bg = Image.open(io.BytesIO(background_png)).convert("RGBA")
    cutout = Image.open(io.BytesIO(cutout_png)).convert("RGBA")

    # --- resize background to square canvas ---
    bg = bg.resize((canvas_px, canvas_px), Image.LANCZOS)

    # --- scale cutout to target width while preserving aspect ratio ---
    target_w = int(canvas_px * _CUTOUT_SCALE)
    aspect = cutout.height / cutout.width if cutout.width > 0 else 1.0
    target_h = int(target_w * aspect)

    # Guard: ensure cutout height does not exceed canvas
    if target_h > canvas_px:
        target_h = canvas_px
        target_w = int(target_h / aspect) if aspect > 0 else target_h

    cutout = cutout.resize((target_w, target_h), Image.LANCZOS)

    # --- compute placement: horizontally centered, slightly bottom-biased ---
    paste_x = (canvas_px - target_w) // 2
    paste_y = int(canvas_px * _BOTTOM_BIAS) - target_h // 2
    # Clamp within canvas bounds
    paste_y = max(0, min(paste_y, canvas_px - target_h))

    # --- drop shadow: extract alpha, blur it, tint dark, composite first ---
    shadow = _make_drop_shadow(cutout, shadow_offset=(6, 8), blur_radius=_SHADOW_BLUR_RADIUS)
    shadow_x = paste_x + 4
    shadow_y = paste_y + 6

    # Clamp shadow within canvas bounds
    shadow_x = max(0, min(shadow_x, canvas_px - shadow.width))
    shadow_y = max(0, min(shadow_y, canvas_px - shadow.height))

    bg.alpha_composite(shadow, dest=(shadow_x, shadow_y))

    # --- composite cutout over background ---
    bg.alpha_composite(cutout, dest=(paste_x, paste_y))

    # --- convert to RGB and return PNG bytes ---
    result = bg.convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG", optimize=False)
    logger.info(
        "Composited image: cutout=%dx%d at (%d,%d) on %dx%d canvas → %d bytes",
        target_w, target_h, paste_x, paste_y, canvas_px, canvas_px, buf.tell(),
    )
    return buf.getvalue()


def _make_drop_shadow(cutout: Image.Image, shadow_offset: tuple[int, int], blur_radius: int) -> Image.Image:
    """Create a semi-transparent drop shadow from the cutout's alpha channel.

    Args:
        cutout: RGBA image whose alpha defines the shadow shape.
        shadow_offset: (dx, dy) pixel offset for shadow (unused here — caller handles).
        blur_radius: Gaussian blur radius for softening the shadow.

    Returns:
        RGBA image containing only the blurred shadow, same size as cutout.
    """
    # Extract alpha channel as greyscale mask
    alpha = cutout.getchannel("A")

    # Create dark translucent shadow layer
    shadow_layer = Image.new("RGBA", cutout.size, (0, 0, 0, 0))
    shadow_color = Image.new("RGBA", cutout.size, (0, 0, 0, 100))  # 100/255 opacity
    shadow_layer.paste(shadow_color, mask=alpha)

    # Blur to soften edges
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return shadow_layer
