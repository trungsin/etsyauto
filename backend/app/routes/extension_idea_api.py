"""Extension passive log endpoint — POST /extension/idea.

Called by the Chrome extension content script when a user views an Etsy listing.
Creates or updates an Idea row with source='extension_passive' and optionally
appends an IdeaSignal row when engagement metrics are supplied.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import idea_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["extension"])


class ExtensionIdeaBody(BaseModel):
    """Validated request body for POST /extension/idea."""

    source_listing_id: str = Field(..., max_length=64)
    title: str = Field(..., max_length=500)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    materials: list[str] = Field(default_factory=list)
    taxonomy_id: int | None = None
    who_made: str | None = None
    when_made: str | None = None
    reference_image_url: str | None = Field(default=None, max_length=2000)
    price_cents: int | None = None
    num_favorers: int | None = None
    views_all_time: int | None = None


@router.post("/idea")
def log_extension_idea(
    body: ExtensionIdeaBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> JSONResponse:
    """Passively log an Etsy listing the user viewed, creating or updating an Idea row.

    Idempotent on (source='extension_passive', source_listing_id).
    Appends an IdeaSignal when num_favorers or views_all_time is provided.

    Returns:
        201 on new idea creation.
        200 on duplicate (same idea_id returned).
        Both responses include {idea_id, status, created}.
    """
    try:
        idea, created = idea_service.upsert_idea(
            db,
            source="extension_passive",
            source_listing_id=body.source_listing_id,
            title=body.title,
            description=body.description,
            tags_json=json.dumps(body.tags),
            materials_json=json.dumps(body.materials),
            reference_image_url=body.reference_image_url,
            who_made=body.who_made,
            when_made=body.when_made,
            taxonomy_id=body.taxonomy_id,
            price_cents=body.price_cents,
        )
        if body.num_favorers is not None or body.views_all_time is not None:
            idea_service.append_signal(
                db,
                idea.id,
                num_favorers=body.num_favorers,
                views_all_time=body.views_all_time,
            )
    except Exception as exc:
        logger.exception("extension idea log failed for listing %s", body.source_listing_id)
        raise HTTPException(status_code=500, detail="Failed to log idea") from exc

    status_code = 201 if created else 200
    return JSONResponse(
        content={"idea_id": idea.id, "status": idea.status, "created": created},
        status_code=status_code,
    )
