"""Variation service — atomic bulk CRUD for TemplateVariation rows."""
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.template_variation import TemplateVariation

logger = logging.getLogger(__name__)

MAX_VARIATIONS = 30


def replace_variations(
    session: Session,
    template_id: int,
    variations: list[dict[str, Any]],
) -> list[TemplateVariation]:
    """Atomically replace all variations for a template.

    Deletes existing rows, bulk-inserts new ones, commits in one transaction.

    Args:
        session: Active SQLAlchemy session.
        template_id: Target template id.
        variations: List of dicts with keys: size, color, price_cents, sku (optional).

    Returns:
        List of newly persisted TemplateVariation instances.

    Raises:
        ValueError: If len(variations) > MAX_VARIATIONS.
    """
    if len(variations) > MAX_VARIATIONS:
        raise ValueError(f"Too many variations: {len(variations)} > {MAX_VARIATIONS}")

    # Atomic: delete existing then bulk insert
    session.execute(
        delete(TemplateVariation).where(TemplateVariation.template_id == template_id)
    )

    new_rows = [
        TemplateVariation(
            template_id=template_id,
            size=v["size"],
            color=v["color"],
            price_cents=v["price_cents"],
            sku=v.get("sku"),
        )
        for v in variations
    ]
    session.add_all(new_rows)
    session.commit()

    for row in new_rows:
        session.refresh(row)

    logger.info(
        "Replaced variations for template_id=%d: %d rows",
        template_id,
        len(new_rows),
    )
    return new_rows


def list_variations(session: Session, template_id: int) -> list[TemplateVariation]:
    """Return all variations for a template ordered by id."""
    stmt = (
        select(TemplateVariation)
        .where(TemplateVariation.template_id == template_id)
        .order_by(TemplateVariation.id)
    )
    return list(session.scalars(stmt).all())


def update_variation_price(
    session: Session,
    variation_id: int,
    price_cents: int,
) -> TemplateVariation:
    """Update price_cents on a single variation.

    Raises:
        ValueError: If variation not found.
    """
    variation = session.get(TemplateVariation, variation_id)
    if variation is None:
        raise ValueError(f"Variation {variation_id} not found")
    variation.price_cents = price_cents
    session.commit()
    session.refresh(variation)
    return variation


def clear_variations(session: Session, template_id: int) -> None:
    """Delete all variations for a template."""
    session.execute(
        delete(TemplateVariation).where(TemplateVariation.template_id == template_id)
    )
    session.commit()
    logger.info("Cleared all variations for template_id=%d", template_id)
