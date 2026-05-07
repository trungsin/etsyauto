"""Listings Creator API — POST /listings/from-template (sub-feature C, Phase 3)."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import etsy_error_mapper, listing_creator_service, listing_pre_check

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
    # Optional per-zone design override; falls back to design_id when absent
    zone_designs: dict[str, int] | None = None


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
            zone_designs=body.zone_designs,
        )
    except listing_pre_check.PreCheckFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Listing failed pre-check",
                "issues": [
                    {"field": i.field, "message": i.message} for i in exc.issues
                ],
            },
        ) from exc
    except httpx.HTTPStatusError as exc:
        mapped = etsy_error_mapper.map_etsy_error(exc)
        logger.warning(
            "Etsy %s for template=%d design=%d: %s | raw=%s",
            mapped.category, body.template_id, body.design_id,
            mapped.user_message, (exc.response.text or "")[:500],
        )
        raise HTTPException(status_code=mapped.http_status, detail=mapped.user_message) from exc
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
