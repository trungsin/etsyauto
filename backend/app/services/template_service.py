"""Template service — CRUD operations for Template, TemplateVariation, Design."""
import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.template import Template
from app.models.template_variation import TemplateVariation

logger = logging.getLogger(__name__)


def create_template(
    session: Session,
    name: str,
    category: str,
    image_bytes: bytes,
    anchor_dict: dict[str, float],
    default_price_cents: int,
    variation_options: dict[str, Any],
    image_filename: str = "template.png",
) -> Template:
    """Upload base image to R2 and persist template row.

    Args:
        session: Active SQLAlchemy session.
        name: Template display name.
        category: Product category (apparel, drinkware, print, etc.).
        image_bytes: Raw bytes of the base image.
        anchor_dict: Composite anchor {x, y, w, h} — all 0-1 floats.
        default_price_cents: Default price in cents.
        variation_options: Dict with optional 'sizes' and 'colors' lists.
        image_filename: Original filename (used for mime-type detection).

    Returns:
        Persisted Template instance.
    """
    from app.clients.r2_storage_client import R2StorageClient

    ext = image_filename.rsplit(".", 1)[-1].lower() if "." in image_filename else "png"
    r2_key = f"templates/{uuid.uuid4().hex}.{ext}"

    r2 = R2StorageClient()
    public_url = r2.upload_image(image_bytes, r2_key)

    template = Template(
        name=name,
        category=category,
        base_image_url=public_url,
        composite_anchor_json=json.dumps(anchor_dict),
        default_price_cents=default_price_cents,
        variation_options_json=json.dumps(variation_options),
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    logger.info("Created template id=%d name=%s", template.id, name)
    return template


def list_templates(session: Session) -> list[Template]:
    """Return all templates ordered by id desc."""
    from sqlalchemy import select
    stmt = select(Template).order_by(Template.id.desc())
    return list(session.scalars(stmt).all())


def get_template(session: Session, template_id: int) -> Template | None:
    """Return a single template by id, or None if not found."""
    return session.get(Template, template_id)


def update_template(session: Session, template_id: int, **fields: Any) -> Template:
    """Update mutable fields on a template.

    Allowed fields: name, category, composite_anchor_json,
    default_price_cents, variation_options_json.

    Raises:
        ValueError: If template not found.
    """
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    allowed = {"name", "category", "composite_anchor_json", "default_price_cents", "variation_options_json"}
    for key, value in fields.items():
        if key in allowed:
            setattr(template, key, value)

    session.commit()
    session.refresh(template)

    # Invalidate composite cache since anchor or other params may have changed
    try:
        from app.services import composite_service
        composite_service.invalidate_composites_for_template(template_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Composite cache invalidation failed for template %d: %s", template_id, exc)

    return template


def delete_template(session: Session, template_id: int) -> None:
    """Delete template + cascade variations + delete R2 base image.

    R2 deletion failure is logged as warning but does not abort DB delete.

    Raises:
        ValueError: If template not found.
    """
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    # Extract R2 key from URL before deletion
    base_url = template.base_image_url
    r2_key: str | None = None
    try:
        from app.config import settings
        public_prefix = settings.r2_public_url.rstrip("/") + "/"
        if base_url.startswith(public_prefix):
            r2_key = base_url[len(public_prefix):]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not extract R2 key from URL %s: %s", base_url, exc)

    # Invalidate composite cache before DB delete
    try:
        from app.services import composite_service
        composite_service.invalidate_composites_for_template(template_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Composite cache invalidation failed for template %d: %s", template_id, exc)

    # DB delete (cascade removes variations via FK)
    session.delete(template)
    session.commit()
    logger.info("Deleted template id=%d", template_id)

    # Best-effort R2 cleanup
    if r2_key:
        try:
            from app.clients.r2_storage_client import R2StorageClient
            R2StorageClient().delete_object(r2_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 delete failed for template %d key=%s: %s", template_id, r2_key, exc)


def get_variation_count(session: Session, template_id: int) -> int:
    """Return number of variations for a template."""
    from sqlalchemy import func, select
    stmt = select(func.count()).where(TemplateVariation.template_id == template_id)
    return session.scalar(stmt) or 0
