"""Composite service — R2-cached template × design compositing with invalidation."""
import json
import logging
from io import BytesIO

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "composites/"


def _cache_key(template_id: int, design_id: int) -> str:
    return f"{_CACHE_PREFIX}{template_id}-{design_id}.png"


def get_or_create_composite(
    session: Session,
    template_id: int,
    design_id: int,
) -> tuple[str, bool]:
    """Return composite URL, creating and caching if needed.

    Args:
        session: Active SQLAlchemy session.
        template_id: Template to use as base.
        design_id: Design (must not be source_type='reference_only').

    Returns:
        (composite_url, cached) — cached=True if R2 object already existed.

    Raises:
        ValueError: If template/design not found or design is reference_only.
    """
    from app.clients.r2_storage_client import R2StorageClient
    from app.models.design import Design
    from app.models.template import Template

    # Validate template
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    # Validate design
    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")
    if design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in composite preview")

    key = _cache_key(template_id, design_id)
    r2 = R2StorageClient()

    # Cache hit check
    if r2.object_exists(key):
        url = r2.get_public_url(key)
        logger.info("Composite cache hit: %s", key)
        return url, True

    # Download template base image
    import urllib.request
    try:
        with urllib.request.urlopen(template.base_image_url) as resp:  # noqa: S310
            base_bytes = resp.read()
    except Exception as exc:
        raise ValueError(f"Failed to download template base image: {exc}") from exc

    # Download design image
    try:
        with urllib.request.urlopen(design.file_url) as resp:  # noqa: S310
            design_bytes = resp.read()
    except Exception as exc:
        raise ValueError(f"Failed to download design image: {exc}") from exc

    # Parse anchor
    anchor = json.loads(template.composite_anchor_json) if template.composite_anchor_json else {
        "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0
    }

    # Composite
    from app.services.image_composite import composite_with_anchor
    output_bytes = composite_with_anchor(base_bytes, design_bytes, anchor)

    # Upload to R2 cache
    url = r2.upload_image(output_bytes, key)
    logger.info("Composite created and cached: %s (%d bytes)", key, len(output_bytes))
    return url, False


def invalidate_composites_for_template(template_id: int) -> int:
    """Delete all cached composites for a template from R2.

    Args:
        template_id: Template whose composites should be invalidated.

    Returns:
        Number of objects deleted.
    """
    from app.clients.r2_storage_client import R2StorageClient
    r2 = R2StorageClient()
    prefix = f"{_CACHE_PREFIX}{template_id}-"
    keys = r2.list_objects(prefix)
    count = 0
    for key in keys:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info("Invalidated %d composite(s) for template %d", count, template_id)
    return count


def invalidate_composites_for_design(design_id: int) -> int:
    """Delete all cached composites involving a design from R2.

    Args:
        design_id: Design whose composites should be invalidated.

    Returns:
        Number of objects deleted.
    """
    from app.clients.r2_storage_client import R2StorageClient
    r2 = R2StorageClient()
    # List all composites and filter by design_id suffix
    prefix = _CACHE_PREFIX
    all_keys = r2.list_objects(prefix)
    suffix = f"-{design_id}.png"
    keys_to_delete = [k for k in all_keys if k.endswith(suffix)]
    count = 0
    for key in keys_to_delete:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info("Invalidated %d composite(s) for design %d", count, design_id)
    return count
