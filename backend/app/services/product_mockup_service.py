"""Product mockup service — generates Etsy-ready mockups via genmockup quad pipeline.

Wraps the embedded genmockup_pipeline package (composite + template registry).
AI fallback is not used; artwork must already have background removed.
"""
import logging
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image

from app.config import settings
from app.genmockup_pipeline.composite import composite
from app.genmockup_pipeline.marketplace_postprocess import postprocess_etsy
from app.genmockup_pipeline.template_registry import Template, TemplateRegistry
from app.services.image_service import save_to_static, upload_to_r2

logger = logging.getLogger(__name__)

ProductType = Literal["tshirt", "poster", "canvas", "pillow"]

_registry: TemplateRegistry | None = None


def _get_registry() -> TemplateRegistry:
    """Return singleton TemplateRegistry; reload on first call or if root changes."""
    global _registry
    if _registry is None:
        root = _template_root()
        logger.info("Loading product mockup templates from %s", root)
        _registry = TemplateRegistry(root)
        if _registry.errors:
            for err in _registry.errors:
                logger.warning("Template load error: %s", err)
        logger.info("Loaded %d product mockup templates", len(_registry.templates))
    return _registry


def _template_root() -> Path:
    return Path(settings.genmockup_template_root)


def _render_template(template: Template, artwork: Image.Image) -> bytes:
    """Composite artwork onto template, postprocess to Etsy 2000×2000 JPEG."""
    meta_dict = template.meta.model_dump()
    # Load mask via copy() so the file handle is closed immediately after pixel data is read
    if template.mask_path:
        with Image.open(template.mask_path) as mask_file:
            meta_dict["mask_image"] = mask_file.copy()
    with Image.open(template.base_path) as base:
        result = composite(base, artwork, meta_dict)
    return postprocess_etsy(result)


def generate_product_mockups(
    artwork_bytes: bytes,
    products: list[ProductType],
    colors: dict[str, list[str]] | None = None,
    angles: list[str] | None = None,
) -> list[str]:
    """Generate Etsy-ready 2000×2000 JPEG mockups for the given product types.

    Args:
        artwork_bytes: PNG/JPEG bytes with background already removed (transparent alpha).
        products: Product types to render, e.g. ["tshirt", "poster"].
        colors: Optional per-product color filter, e.g. {"tshirt": ["white"]}.
        angles: Optional angle filter applied across all products.

    Returns:
        List of accessible image URLs (R2 public URL or local /static/ path).
    """
    registry = _get_registry()
    artwork = Image.open(BytesIO(artwork_bytes)).convert("RGBA")

    output_urls: list[str] = []
    for product in products:
        product_colors = (colors or {}).get(product)
        color_list: list[str | None] = product_colors if product_colors else [None]
        angle_list: list[str | None] = angles if angles else [None]

        for color in color_list:
            for angle in angle_list:
                templates = registry.find(product, angle=angle, color=color)
                if not templates:
                    logger.warning("No template for product=%s angle=%s color=%s", product, angle, color)
                    continue
                for tmpl in templates:
                    try:
                        jpeg_bytes = _render_template(tmpl, artwork)
                    except Exception:
                        logger.exception("Failed to render template %s", tmpl.id)
                        continue
                    local_path = save_to_static(jpeg_bytes, ext="jpg")
                    r2_url = upload_to_r2(local_path)
                    output_urls.append(r2_url or f"/static/{Path(local_path).name}")

    return output_urls


def list_available_products() -> list[str]:
    """Return product types that have at least one template loaded."""
    return _get_registry().list_products()


def reload_registry() -> int:
    """Force-reload the template registry (e.g. after adding new templates). Returns template count."""
    global _registry
    _registry = None
    return len(_get_registry().templates)
