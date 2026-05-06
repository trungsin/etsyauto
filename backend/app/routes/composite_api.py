"""Composite preview API — generate or return cached template × design composite."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import composite_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/composite", tags=["composite"])


class CompositeRequest(BaseModel):
    template_id: int
    design_id: int

    @field_validator("template_id", "design_id")
    @classmethod
    def must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ID must be a positive integer")
        return v


@router.post("/preview")
def composite_preview(
    body: CompositeRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Generate (or return cached) composite of template base + design.

    Returns:
        200 {"composite_url": "...", "cached": true|false}
        400 if template/design not found or design is reference_only.
        500 on compositing error.
    """
    try:
        composite_url, cached = composite_service.get_or_create_composite(
            session=db,
            template_id=body.template_id,
            design_id=body.design_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Composite failed for template=%d design=%d", body.template_id, body.design_id
        )
        raise HTTPException(status_code=500, detail="Composite generation failed") from exc

    return {"composite_url": composite_url, "cached": cached}


@router.post("/preview-all-colors")
def composite_preview_all_colors(
    body: CompositeRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Render composites for every color in template.variation_options.colors in parallel.

    Returns:
        200 {"results": [{color, composite_url, cached, error}, ...]}
        400 if template/design not found, design reference_only, or no colors defined.
        500 on unexpected failure.
    """
    try:
        results = composite_service.get_or_create_composites_all_colors(
            session=db,
            template_id=body.template_id,
            design_id=body.design_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Composite-all failed for template=%d design=%d", body.template_id, body.design_id
        )
        raise HTTPException(status_code=500, detail="Composite generation failed") from exc

    return {"results": results}
