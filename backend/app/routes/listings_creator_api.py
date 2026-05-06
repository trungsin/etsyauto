"""Listings Creator API — POST /listings/from-template (sub-feature C, Phase 3)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import listing_creator_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/listings", tags=["listings"])


class ComboInput(BaseModel):
    size: str
    color: str
    enabled: bool = True


class FromTemplateBody(BaseModel):
    template_id: int = Field(..., gt=0)
    design_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=1, max_length=140)  # Etsy title cap
    description: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    enabled_combos: list[ComboInput] = Field(default_factory=list)
    shop_id: str | int
    quantity_per_variant: int = Field(default=100, ge=1, le=999)


@router.post(
    "/from-template",
    status_code=201,
    dependencies=[Depends(require_admin_token)],
)
def create_listing_from_template(
    body: FromTemplateBody,
    db: Session = Depends(get_db),
) -> dict:
    """Create an Etsy draft listing from a template + design with full variations matrix.

    Idempotent on (template_id, design_id): subsequent calls return the existing
    Listing's etsy_listing_id without re-creating.
    """
    try:
        result = listing_creator_service.create_from_template(
            session=db,
            template_id=body.template_id,
            design_id=body.design_id,
            title=body.title,
            description=body.description,
            tags=body.tags,
            enabled_combos=[c.model_dump() for c in body.enabled_combos],
            shop_id=body.shop_id,
            quantity_per_variant=body.quantity_per_variant,
        )
    except ValueError as exc:
        detail = str(exc)
        # Map common errors to status codes
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail) from exc
        lower = detail.lower()
        if (
            "too many" in lower
            or "no enabled" in lower
            or "no matching" in lower
            or "no value" in lower  # Etsy taxonomy resolve mismatch
        ):
            raise HTTPException(status_code=422, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        logger.exception(
            "Listing creator failed for template=%d design=%d",
            body.template_id, body.design_id,
        )
        raise HTTPException(status_code=502, detail=f"Etsy listing creation failed: {exc}") from exc

    return result
