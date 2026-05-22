"""Blend modes for compositing artwork onto template base images."""
from PIL import Image, ImageChops


def _alpha_composite(base: Image.Image, top_rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    return Image.composite(top_rgb.convert("RGBA"), base.convert("RGBA"), alpha)


def blend(base: Image.Image, top: Image.Image, mode: str) -> Image.Image:
    base_rgba = base.convert("RGBA")
    top_rgba = top.convert("RGBA")
    alpha = top_rgba.getchannel("A")
    if mode == "normal":
        candidate = top_rgba
    elif mode == "multiply":
        candidate = ImageChops.multiply(base_rgba.convert("RGB"), top_rgba.convert("RGB")).convert("RGBA")
    elif mode == "screen":
        candidate = ImageChops.screen(base_rgba.convert("RGB"), top_rgba.convert("RGB")).convert("RGBA")
    else:
        raise ValueError(f"unsupported blend mode: {mode}")
    return _alpha_composite(base_rgba, candidate, alpha)
