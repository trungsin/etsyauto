"""Ingest route — receives a listing_id from the Chrome extension and queues it.

POST /ingest
  Body: {listing_id: int, source_url: str, title: str|None, images: list[str]|None}
  Returns: {job_id: int, listing_id: int, status: str}

Idempotent: if etsy_listing_id already exists with status not in ('pushed', 'failed'),
return the existing row unchanged.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.listing import Listing

logger = logging.getLogger(__name__)
router = APIRouter()

# Statuses that are considered "terminal" — re-ingest allowed
_TERMINAL_STATUSES = {"pushed", "failed"}


class IngestRequest(BaseModel):
    listing_id: int = Field(..., gt=0, description="Etsy numeric listing ID")
    source_url: str = Field(..., min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    images: list[str] | None = Field(default=None)


class IngestResponse(BaseModel):
    job_id: int
    listing_id: int
    status: str


@router.post("/ingest", response_model=IngestResponse, tags=["ingest"])
def ingest_listing(body: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    """Queue an Etsy listing for optimization.

    If the listing already exists and is not in a terminal state (pushed/failed),
    return the existing record (idempotent). Otherwise insert a fresh row.
    """
    etsy_id_str = str(body.listing_id)

    existing: Listing | None = (
        db.query(Listing).filter(Listing.etsy_listing_id == etsy_id_str).first()
    )

    if existing and existing.status not in _TERMINAL_STATUSES:
        logger.info(
            "Listing %s already queued (id=%s, status=%s) — returning existing.",
            etsy_id_str, existing.id, existing.status,
        )
        return IngestResponse(
            job_id=existing.id,
            listing_id=body.listing_id,
            status=existing.status,
        )

    images_json: str | None = None
    if body.images:
        images_json = json.dumps(body.images)

    # Use provided title as a hint; backend will overwrite with Etsy API data later.
    title_value = body.title or f"Listing {etsy_id_str}"

    if existing:
        # Existing row is in a terminal state — reset it for re-optimization.
        existing.original_title = title_value
        existing.original_images = images_json
        existing.status = "new"
        row = existing
    else:
        row = Listing(
            etsy_listing_id=etsy_id_str,
            original_title=title_value,
            original_images=images_json,
            status="new",
        )
        db.add(row)

    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("DB error ingesting listing %s: %s", etsy_id_str, exc)
        raise HTTPException(status_code=500, detail="Database error during ingest.") from exc

    logger.info("Ingested listing %s → job_id=%s", etsy_id_str, row.id)
    return IngestResponse(job_id=row.id, listing_id=body.listing_id, status=row.status)
