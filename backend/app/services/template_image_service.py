"""Service layer for TemplateImage CRUD + legacy virtualization.

Virtual rows are produced on-the-fly from `Template.base_image_url` +
`color_base_images_json` when the `template_images` table has no rows for
that template, preserving backward compat with pre-image-pool templates.
"""
from __future__ import annotations

import json
import logging
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.template import Template
from app.models.template_image import TemplateImage
from app.services.anchor_schema import validate_for_image

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_TEMPLATE = 20
VALID_ROLES: frozenset[str] = frozenset({"mockup", "lifestyle_no_fill"})


class TemplateImageView(TypedDict):
    id: int | None  # None for virtual rows
    template_id: int
    image_url: str
    color: str | None  # title-cased; None means universal
    rank: int
    anchor_json: str
    role: Literal["mockup", "lifestyle_no_fill"]
    is_virtual: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_empty_anchor(raw: str | None) -> bool:
    """True when anchor_json is missing or has no usable zones."""
    if not raw:
        return True
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(data, dict):
        return True
    if isinstance(data.get("zones"), list) and data["zones"]:
        return False
    if all(k in data for k in ("x", "y", "w", "h")):  # legacy v1 rect
        return False
    return True


def _first_non_empty_anchor(session: Session, template_id: int) -> str | None:
    """Return the lowest-rank mockup's anchor_json in this template, or None."""
    rows = session.scalars(
        select(TemplateImage)
        .where(TemplateImage.template_id == template_id)
        .order_by(TemplateImage.rank.asc())
    ).all()
    for r in rows:
        if r.role == "mockup" and not _is_empty_anchor(r.anchor_json):
            return r.anchor_json
    return None


def _row_to_view(row: TemplateImage) -> TemplateImageView:
    return TemplateImageView(
        id=row.id,
        template_id=row.template_id,
        image_url=row.image_url,
        color=row.color,
        rank=row.rank,
        anchor_json=row.anchor_json,
        role=row.role,  # type: ignore[arg-type]
        is_virtual=False,
    )


def _virtualize(tpl: Template | None) -> list[TemplateImageView]:
    """Produce virtual TemplateImageView rows from legacy Template fields."""
    if tpl is None or not tpl.base_image_url:
        return []

    base_anchor = tpl.composite_anchor_json or "{}"
    out: list[TemplateImageView] = [
        TemplateImageView(
            id=None,
            template_id=tpl.id,
            image_url=tpl.base_image_url,
            color=None,
            rank=0,
            anchor_json=base_anchor,
            role="mockup",
            is_virtual=True,
        )
    ]

    try:
        color_bases: dict[str, str] = json.loads(tpl.color_base_images_json or "{}")
    except (json.JSONDecodeError, TypeError):
        color_bases = {}

    for i, (color, url) in enumerate(color_bases.items(), start=1):
        out.append(
            TemplateImageView(
                id=None,
                template_id=tpl.id,
                image_url=url,
                color=color.title(),
                rank=i,
                anchor_json=base_anchor,
                role="mockup",
                is_virtual=True,
            )
        )

    return out


def _allowed_colors(tpl: Template) -> set[str]:
    """Return title-cased set of allowed colors from template.variation_options_json."""
    try:
        opts = json.loads(tpl.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    return {c.strip().title() for c in opts.get("colors", [])}


def _validate_inputs(
    *,
    image_url: str | None = None,
    color: str | None = None,
    rank: int | None = None,
    anchor_json: str | None = None,
    role: str | None = None,
    tpl: Template | None = None,
) -> None:
    """Validate field values; raise ValueError with descriptive message on failure."""
    if image_url is not None and not image_url.strip():
        raise ValueError("image_url must not be empty")

    if role is not None and role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {role!r}")

    if rank is not None and rank < 0:
        raise ValueError(f"rank must be >= 0, got {rank}")

    if anchor_json is not None:
        validate_for_image(anchor_json)  # raises ValueError if > MAX_ZONES_PER_IMAGE

    if color is not None and tpl is not None:
        color_norm = color.strip().title()
        allowed = _allowed_colors(tpl)
        if allowed and color_norm not in allowed:
            raise ValueError(
                f"Color {color_norm!r} not in template variation_options.colors {sorted(allowed)}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_for_template(session: Session, template_id: int) -> list[TemplateImageView]:
    """Return real rows if any exist; otherwise virtualize from legacy fields."""
    rows = session.scalars(
        select(TemplateImage)
        .where(TemplateImage.template_id == template_id)
        .order_by(TemplateImage.rank)
    ).all()
    if rows:
        return [_row_to_view(r) for r in rows]
    return _virtualize(session.get(Template, template_id))


def get(session: Session, image_id: int) -> TemplateImageView | None:
    """Return a single real TemplateImage by id, or None if not found."""
    row = session.get(TemplateImage, image_id)
    return _row_to_view(row) if row else None


def get_view_by_color(
    session: Session, template_id: int, color: str | None
) -> TemplateImageView | None:
    """Return the first image matching template+color, from real rows or virtual rows.

    Used by composite back-compat path to locate the appropriate base image.
    color=None matches the universal (no-color) row.
    """
    color_norm = color.strip().title() if color else None
    stmt = (
        select(TemplateImage)
        .where(TemplateImage.template_id == template_id)
        .where(TemplateImage.color == color_norm)
        .order_by(TemplateImage.rank)
        .limit(1)
    )
    row = session.scalars(stmt).first()
    if row:
        return _row_to_view(row)

    # Fall back to virtual rows
    virtual = _virtualize(session.get(Template, template_id))
    for v in virtual:
        if v["color"] == color_norm:
            return v
    return None


def create(
    session: Session,
    *,
    template_id: int,
    image_url: str,
    color: str | None = None,
    rank: int = 0,
    anchor_json: str = "{}",
    role: str = "mockup",
) -> TemplateImageView:
    """Create a new TemplateImage row.

    Raises:
        ValueError: On validation failure or capacity limit reached.
    """
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise ValueError(f"Template {template_id} not found")

    color_norm = color.strip().title() if color else None

    # Auto-inherit anchor from an existing mockup in the same template when caller
    # didn't provide one. Saves the user from having to manually set zones on every
    # upload, and prevents "no fill zones" silently dropping the image from the
    # listing-creator gallery.
    if role == "mockup" and _is_empty_anchor(anchor_json):
        inherited = _first_non_empty_anchor(session, template_id)
        if inherited is not None:
            anchor_json = inherited

    _validate_inputs(
        image_url=image_url,
        color=color_norm,
        rank=rank,
        anchor_json=anchor_json,
        role=role,
        tpl=tpl,
    )

    existing_count = session.scalar(
        select(func.count()).where(TemplateImage.template_id == template_id)
    ) or 0
    if existing_count >= MAX_IMAGES_PER_TEMPLATE:
        raise ValueError(
            f"Template {template_id} already has {existing_count} images "
            f"(max {MAX_IMAGES_PER_TEMPLATE})"
        )

    row = TemplateImage(
        template_id=template_id,
        image_url=image_url.strip(),
        color=color_norm,
        rank=rank,
        anchor_json=anchor_json,
        role=role,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"rank {rank} already used in template {template_id}") from exc

    session.refresh(row)
    logger.info("Created TemplateImage id=%d template=%d rank=%d", row.id, template_id, rank)
    return _row_to_view(row)


def update(
    session: Session,
    image_id: int,
    *,
    image_url: str | None = None,
    color: str | None = None,
    rank: int | None = None,
    anchor_json: str | None = None,
    role: str | None = None,
) -> TemplateImageView:
    """Update mutable fields on a TemplateImage.

    Raises:
        ValueError: If image not found or validation fails.
    """
    row = session.get(TemplateImage, image_id)
    if row is None:
        raise ValueError(f"TemplateImage {image_id} not found")

    tpl = session.get(Template, row.template_id)
    color_norm = color.strip().title() if color is not None else None

    _validate_inputs(
        image_url=image_url,
        color=color_norm,
        rank=rank,
        anchor_json=anchor_json,
        role=role,
        tpl=tpl,
    )

    if image_url is not None:
        row.image_url = image_url.strip()
    if color is not None:
        row.color = color_norm
    if rank is not None:
        row.rank = rank
    if anchor_json is not None:
        row.anchor_json = anchor_json
    if role is not None:
        row.role = role

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(f"rank {rank} already used in template {row.template_id}") from exc

    session.refresh(row)
    logger.info("Updated TemplateImage id=%d template=%d", image_id, row.template_id)
    return _row_to_view(row)


def delete(session: Session, image_id: int) -> bool:
    """Delete a TemplateImage. Returns True if deleted, False if not found."""
    row = session.get(TemplateImage, image_id)
    if row is None:
        return False
    template_id = row.template_id
    session.delete(row)
    session.commit()
    logger.info("Deleted TemplateImage id=%d template=%d", image_id, template_id)
    return True


def reorder(session: Session, template_id: int, items: list[dict]) -> int:
    """Bulk-update rank for images belonging to template_id.

    items format: [{"id": int, "rank": int}, ...]
    Uses two-pass swap to avoid UNIQUE(template_id, rank) collision.

    Raises:
        ValueError: If any id does not belong to template_id.
    Returns:
        Number of rows updated.
    """
    if not items:
        return 0

    ids = [it["id"] for it in items]
    rows = session.scalars(
        select(TemplateImage).where(TemplateImage.id.in_(ids))
    ).all()

    row_map = {r.id: r for r in rows}
    for it in items:
        r = row_map.get(it["id"])
        if r is None or r.template_id != template_id:
            raise ValueError(
                f"TemplateImage id={it['id']} does not belong to template {template_id}"
            )

    # Pass 1: set negative temp ranks to dodge UNIQUE collision
    for r in rows:
        r.rank = -(r.id)
    session.flush()

    # Pass 2: apply target ranks
    rank_map = {it["id"]: it["rank"] for it in items}
    for r in rows:
        r.rank = rank_map[r.id]

    session.commit()
    return len(rows)


def materialize(session: Session, template_id: int) -> int:
    """Convert virtual rows to real DB rows. Idempotent — returns 0 if already materialized."""
    existing = session.scalar(
        select(func.count()).where(TemplateImage.template_id == template_id)
    ) or 0
    if existing > 0:
        return 0

    virtual = _virtualize(session.get(Template, template_id))
    for v in virtual:
        session.add(
            TemplateImage(
                template_id=v["template_id"],
                image_url=v["image_url"],
                color=v["color"],
                rank=v["rank"],
                anchor_json=v["anchor_json"],
                role=v["role"],
            )
        )
    session.commit()
    logger.info("Materialized %d virtual images for template %d", len(virtual), template_id)
    return len(virtual)
