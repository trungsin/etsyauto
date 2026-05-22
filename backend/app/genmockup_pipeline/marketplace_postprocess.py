"""Marketplace-specific output sizing for generated mockup images."""
import io
import re

from PIL import Image


PRESETS: dict[str, dict] = {
    "etsy": {"width": 2000, "height": 2000, "fit": "contain", "quality": 92},
    "tiktok-square": {"width": 1080, "height": 1080, "fit": "contain", "quality": 92},
    "tiktok-vertical": {"width": 1080, "height": 1920, "fit": "cover", "quality": 92},
}


def resize_for(image: Image.Image, preset: dict) -> Image.Image:
    target = (int(preset["width"]), int(preset["height"]))
    source = image.convert("RGB")
    if preset.get("fit") == "cover":
        ratio = max(target[0] / source.width, target[1] / source.height)
        resized = source.resize((round(source.width * ratio), round(source.height * ratio)))
        left = (resized.width - target[0]) // 2
        top = (resized.height - target[1]) // 2
        return resized.crop((left, top, left + target[0], top + target[1]))
    copy = source.copy()
    copy.thumbnail(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, "white")
    canvas.paste(copy, ((target[0] - copy.width) // 2, (target[1] - copy.height) // 2))
    return canvas


def _slug(value: str | None) -> str:
    text = value or "default"
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "default"


def slug_filename(product: str, color: str | None, angle: str, marketplace: str) -> str:
    return f"{_slug(product)}-{_slug(color)}-{_slug(angle)}-{_slug(marketplace)}.jpg"


def postprocess_etsy(image: Image.Image) -> bytes:
    """Resize image to Etsy 2000×2000 contain and return JPEG bytes."""
    resized = resize_for(image, PRESETS["etsy"])
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()
