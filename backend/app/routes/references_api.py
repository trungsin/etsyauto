"""References JSON API — scrape, get, list, update, delete reference listings. Admin-protected."""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

try:
    from google.genai import errors as _genai_errors  # type: ignore[import-untyped]
    _GEMINI_TRANSIENT = (
        getattr(_genai_errors, "ServerError", None),
        getattr(_genai_errors, "APIError", None),
    )
    _GEMINI_TRANSIENT = tuple(e for e in _GEMINI_TRANSIENT if e is not None)
except Exception:  # noqa: BLE001
    _GEMINI_TRANSIENT = ()

from app.config import settings
from app.database import get_db
from app.dependencies import require_admin_token
from app.services import reference_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/references", tags=["references"])

_VALID_TAGS = {"style", "color", "layout", "season", "niche"}


class ScrapeBody(BaseModel):
    """Request body for POST /references/scrape."""

    source_url: str = Field(..., max_length=500)
    source_listing_id: str = Field(..., max_length=50)
    original_title: str | None = Field(default=None, max_length=500)
    original_description: str | None = None
    original_images: list[str] = Field(default_factory=list)

    @field_validator("original_images")
    @classmethod
    def max_ten_images(cls, v: list[str]) -> list[str]:
        if len(v) > 10:
            raise ValueError(f"Too many images: {len(v)} > 10 allowed")
        return v


class UpdateBody(BaseModel):
    """Request body for PUT /references/{id} — all fields optional."""

    edited_title: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    tags: list[str] | None = None
    kept_image_indices: list[int] | None = None
    status: str | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        invalid = set(v) - _VALID_TAGS
        if invalid:
            raise ValueError(f"Invalid tags: {invalid}. Allowed: {_VALID_TAGS}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"scraped", "enriched", "saved"}
        if v not in allowed:
            raise ValueError(f"Invalid status '{v}'. Allowed: {allowed}")
        return v


def _serialize_reference(ref: Any) -> dict:
    """Convert a Reference ORM row to a JSON-serializable dict."""
    return {
        "id": ref.id,
        "source_url": ref.source_url,
        "source_listing_id": ref.source_listing_id,
        "original_title": ref.original_title,
        "original_description": ref.original_description,
        "original_images": _safe_json_loads(ref.original_images_json, []),
        "ai_title_variants": _safe_json_loads(ref.ai_title_variants_json, None),
        "edited_title": ref.edited_title,
        "notes": ref.notes,
        "tags": _safe_json_loads(ref.tags_json, []),
        "kept_image_indices": _safe_json_loads(ref.kept_image_indices_json, []),
        "cutout_design_id": ref.cutout_design_id,
        "notion_page_id": ref.notion_page_id,
        "status": ref.status,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
        "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
    }


def _safe_json_loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


@router.post("/scrape", status_code=201)
def scrape_reference(
    body: ScrapeBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Scrape a new reference listing (idempotent on source_listing_id).

    Returns:
        201 on new creation, 200 on duplicate (same id returned).
    """
    try:
        ref, created = reference_service.create_or_get_reference(
            session=db,
            source_url=body.source_url,
            source_listing_id=body.source_listing_id,
            original_title=body.original_title,
            original_description=body.original_description,
            original_images=body.original_images,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to scrape reference")
        raise HTTPException(status_code=500, detail="Internal error creating reference") from exc

    from fastapi.responses import JSONResponse
    status_code = 201 if created else 200
    return JSONResponse(content=_serialize_reference(ref), status_code=status_code)


@router.get("")
def list_references(
    tags: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """List references, optionally filtered by tags (comma-separated) or status.

    Returns:
        200 {references: [...]}
    """
    refs = reference_service.list_references(db, tags=tags, status=status)
    return {"references": [_serialize_reference(r) for r in refs]}


@router.get("/{reference_id}")
def get_reference(
    reference_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Get a single reference by id.

    Returns:
        200 reference dict.
        404 if not found.
    """
    ref = reference_service.get_reference(db, reference_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Reference {reference_id} not found")
    return _serialize_reference(ref)


@router.put("/{reference_id}")
def update_reference(
    reference_id: int,
    body: UpdateBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Partially update a reference (only provided fields are changed).

    Returns:
        200 updated reference dict.
        404 if not found.
        400 on invalid field values.
    """
    # Build kwargs from only explicitly provided fields (exclude unset)
    updates = body.model_dump(exclude_unset=True)
    try:
        ref = reference_service.update_reference(db, reference_id, **updates)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        logger.exception("Failed to update reference %d", reference_id)
        raise HTTPException(status_code=500, detail="Internal error updating reference") from exc
    return _serialize_reference(ref)


class CutoutBody(BaseModel):
    """Request body for POST /references/{id}/cutout."""

    image_url: str = Field(..., max_length=2000)
    crop_box: list[float] | None = Field(
        default=None,
        description="Optional [x, y, w, h] in 0-1 fractions of source image dims",
    )

    @field_validator("crop_box")
    @classmethod
    def validate_crop_box(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        if len(v) != 4:
            raise ValueError("crop_box must have 4 elements: [x, y, w, h]")
        x, y, w, h = v
        for name, val in zip(("x", "y", "w", "h"), v):
            if not 0 <= val <= 1:
                raise ValueError(f"crop_box.{name}={val} must be a 0-1 fraction")
        if w <= 0 or h <= 0:
            raise ValueError("crop_box width and height must be > 0")
        if x + w > 1.001 or y + h > 1.001:
            raise ValueError("crop_box extends beyond image bounds")
        return v


def _is_transient_gemini_error(exc: Exception) -> bool:
    """Return True for Gemini 503/429/UNAVAILABLE errors that warrant a 503 response."""
    if _GEMINI_TRANSIENT and isinstance(exc, _GEMINI_TRANSIENT):
        msg = str(exc).upper()
        return any(token in msg for token in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
    return False


@router.post("/{reference_id}/suggest-title")
def suggest_title(
    reference_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Generate 3 AI title variants for a reference listing (idempotent, replaces existing).

    Returns:
        200 {variants: [...]}
        404 if reference not found.
        503 on transient Gemini quota/availability errors.
    """
    try:
        variants = reference_service.generate_title_variants(db, reference_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        if _is_transient_gemini_error(exc):
            logger.warning("Transient Gemini error for reference %d: %s", reference_id, exc)
            raise HTTPException(
                status_code=503,
                detail="AI service temporarily unavailable. Retry later.",
                headers={"Retry-After": "30"},
            ) from exc
        logger.exception("Failed to generate title variants for reference %d", reference_id)
        raise HTTPException(status_code=500, detail="Internal error generating title variants") from exc
    return {"variants": variants}


@router.post("/{reference_id}/cutout")
def create_cutout(
    reference_id: int,
    body: CutoutBody,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Download image, strip background, create Design row, and link to reference.

    Returns:
        200 {design_id, file_url}
        400 if image_url not in reference's original_images.
        404 if reference not found.
    """
    try:
        design = reference_service.create_cutout(
            db, reference_id, body.image_url, crop_box=body.crop_box
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        logger.exception("Failed to create cutout for reference %d", reference_id)
        raise HTTPException(status_code=500, detail="Internal error creating cutout") from exc
    return {"design_id": design.id, "file_url": design.file_url}


@router.post("/{reference_id}/save")
def save_reference_to_notion(
    reference_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Push reference to Notion Idea Bank (idempotent — updates if already saved).

    Returns:
        200 {notion_page_id, status: "saved"}
        404 if reference not found.
        503 if NOTION_IDEA_BANK_DATA_SOURCE_ID not configured.
    """
    if not settings.notion_idea_bank_data_source_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "Notion Idea Bank not configured. "
                "Set NOTION_IDEA_BANK_DATA_SOURCE_ID in .env. "
                "See docs/notion-idea-bank-setup.md for setup instructions."
            ),
        )
    try:
        page_id = reference_service.save_to_notion(db, reference_id)
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        logger.exception("Failed to save reference %d to Notion", reference_id)
        raise HTTPException(status_code=500, detail="Internal error saving to Notion") from exc
    return {"notion_page_id": page_id, "status": "saved"}


@router.delete("/{reference_id}", status_code=204)
def delete_reference(
    reference_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> Response:
    """Delete a reference and cascade cleanup (cutout design + Notion archive placeholder).

    Returns:
        204 No Content on success.
        404 if not found.
    """
    try:
        reference_service.delete_reference(db, reference_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete reference %d", reference_id)
        raise HTTPException(status_code=500, detail="Internal error deleting reference") from exc
    return Response(status_code=204)
