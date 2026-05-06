"""Variations JSON API — bulk CRUD for template variations, protected by X-Admin-Token."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import template_service, variation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["variations"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class VariationInput(BaseModel):
    size: str = Field(..., min_length=1, max_length=50)
    color: str = Field(..., min_length=1, max_length=50)
    price_cents: int = Field(..., ge=0)
    sku: str | None = Field(default=None, max_length=100)


class BulkVariationsBody(BaseModel):
    variations: list[VariationInput]


class VariationOut(BaseModel):
    id: int
    template_id: int
    size: str
    color: str
    price_cents: int
    sku: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_template_or_404(db: Session, template_id: int):
    tmpl = template_service.get_template(db, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return tmpl


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/{template_id}/variations",
    dependencies=[Depends(require_admin_token)],
)
def bulk_replace_variations(
    template_id: int,
    body: BulkVariationsBody,
    db: Session = Depends(get_db),
) -> dict:
    """Bulk replace all variations for a template (max 30 rows)."""
    _get_template_or_404(db, template_id)

    if len(body.variations) > 30:
        raise HTTPException(status_code=400, detail="Too many variations: max 30 allowed")

    try:
        rows = variation_service.replace_variations(
            db,
            template_id,
            [v.model_dump() for v in body.variations],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Duplicate (size, color) combination"
        ) from exc

    return {"variations": [VariationOut.model_validate(r) for r in rows]}


@router.get(
    "/{template_id}/variations",
    dependencies=[Depends(require_admin_token)],
)
def list_variations(
    template_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """List all variations for a template."""
    _get_template_or_404(db, template_id)
    rows = variation_service.list_variations(db, template_id)
    return {"variations": [VariationOut.model_validate(r) for r in rows]}


@router.delete(
    "/{template_id}/variations",
    status_code=204,
    dependencies=[Depends(require_admin_token)],
)
def clear_variations(
    template_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Delete all variations for a template."""
    _get_template_or_404(db, template_id)
    variation_service.clear_variations(db, template_id)


@router.put(
    "/{template_id}/variations/{variation_id}",
    dependencies=[Depends(require_admin_token)],
)
def update_variation_price(
    template_id: int,
    variation_id: int,
    body: dict,
    db: Session = Depends(get_db),
) -> VariationOut:
    """Partial update: update price_cents on a single variation."""
    _get_template_or_404(db, template_id)

    price_cents = body.get("price_cents")
    if price_cents is None or not isinstance(price_cents, int) or price_cents < 0:
        raise HTTPException(status_code=422, detail="price_cents must be a non-negative integer")

    try:
        row = variation_service.update_variation_price(db, variation_id, price_cents)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Ensure variation belongs to this template
    if row.template_id != template_id:
        raise HTTPException(status_code=404, detail="Variation not found for this template")

    return VariationOut.model_validate(row)


@router.post(
    "/{template_id}/expand-variations",
    status_code=200,
    dependencies=[Depends(require_admin_token)],
)
def expand_variations(template_id: int, db: Session = Depends(get_db)) -> list[VariationOut]:
    """Auto-build cartesian (size, color) variations from template.variation_options.

    Reads variation_options_json schema and replicates each size's price_cents across
    all colors. Atomically replaces existing variations.

    Returns:
        List of newly created VariationOut rows.
    """
    _get_template_or_404(db, template_id)
    try:
        rows = variation_service.expand_variations(db, template_id)
    except ValueError as exc:
        detail = str(exc)
        status = 422 if "Too many" in detail or "must" in detail else 404
        raise HTTPException(status_code=status, detail=detail) from exc
    return [VariationOut.model_validate(r) for r in rows]
