"""Artwork admin — Cloden Design POD pipeline: upload/crop/refine/removebg/upscale."""
import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import require_admin_token
from app.models.artwork import Artwork
from app.services import artwork_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/artwork", tags=["artwork"])

_jinja: Jinja2Templates | None = None


def _get_jinja() -> Jinja2Templates:
    global _jinja  # noqa: PLW0603
    if _jinja is None:
        from app.main import jinja_templates  # lazy to avoid circular import
        _jinja = jinja_templates
    return _jinja


@router.get("", response_class=HTMLResponse)
def artwork_page(
    request: Request,
    _: None = Depends(require_admin_token),
):
    """Render the Cloden Design POD artwork pipeline admin page."""
    return _get_jinja().TemplateResponse(
        request, "artwork/index.html",
        {"has_openai_key": bool(settings.openai_api_key)},
    )


@router.post("", status_code=201)
async def upload_and_crop(
    file: UploadFile = File(...),
    name: str = Form(...),
    fx: float = Form(...),
    fy: float = Form(...),
    fw: float = Form(...),
    fh: float = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Upload original image and crop to fractional coords. Returns artwork_id + cropped_url."""
    file_bytes = await file.read()
    try:
        artwork = artwork_service.process_upload_and_crop(db, file_bytes, name, fx, fy, fw, fh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": artwork.id, "cropped_url": artwork.cropped_url, "status": artwork.status}


@router.post("/{artwork_id}/refine")
def refine(
    artwork_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Call GPT-Image-1 to clean/smooth the cropped image."""
    try:
        artwork = artwork_service.refine_artwork(db, artwork_id)
    except (ValueError, httpx.HTTPError) as exc:
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"id": artwork.id, "refined_url": artwork.refined_url, "status": artwork.status}


@router.post("/{artwork_id}/skip-refine")
def skip_refine(
    artwork_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Skip GPT refinement — use cropped image directly as refined image."""
    try:
        artwork = artwork_service.skip_refine_artwork(db, artwork_id)
    except ValueError as exc:
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"id": artwork.id, "refined_url": artwork.refined_url, "status": artwork.status}


@router.post("/{artwork_id}/removebg")
def removebg(
    artwork_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Remove background via remove.bg — returns transparent PNG URL."""
    try:
        artwork = artwork_service.removebg_artwork(db, artwork_id)
    except (ValueError, httpx.HTTPError) as exc:
        code = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"id": artwork.id, "removebg_url": artwork.removebg_url, "status": artwork.status}


@router.post("/{artwork_id}/upscale", status_code=202)
def upscale(
    artwork_id: int,
    background_tasks: BackgroundTasks,
    scale: int = Form(4),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Start Real-ESRGAN upscale as a background job. scale=2|3|4. Poll /{id}/status."""
    if scale not in (2, 3, 4):
        raise HTTPException(status_code=400, detail="scale must be 2, 3, or 4")
    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail=f"Artwork {artwork_id} not found")
    if artwork.status != "removebg_done":
        raise HTTPException(
            status_code=400,
            detail=f"Expected status 'removebg_done', got '{artwork.status}'",
        )
    # Mark upscaling before returning to prevent duplicate job spawning
    artwork.status = "upscaling"
    db.commit()
    background_tasks.add_task(artwork_service.run_upscale_job, artwork_id, scale)
    return {"id": artwork_id, "status": "upscaling", "scale": scale}


@router.get("/{artwork_id}/status")
def status(
    artwork_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Poll upscale progress. Returns {status, final_url} when done."""
    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail=f"Artwork {artwork_id} not found")
    return {
        "id": artwork.id,
        "status": artwork.status,
        "final_url": artwork.final_url,
        "error_message": artwork.error_message,
    }
