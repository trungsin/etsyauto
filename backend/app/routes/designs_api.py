"""Designs JSON API — upload, list, delete design assets. Admin-protected."""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import design_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/designs", tags=["designs"])


@router.post("", status_code=201)
async def upload_design(
    file: UploadFile = File(...),
    name: str = Form(...),
    source_type: str = Form("upload"),
    prompt: str | None = Form(default=None),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """Upload a PNG design with alpha channel.

    Returns:
        201 {id, file_url, width, height} on success.
        400 if file is not PNG, has no alpha, or exceeds 10 MB.
    """
    file_bytes = await file.read()
    try:
        design = design_service.create_design(
            session=db,
            file_bytes=file_bytes,
            name=name,
            source_type=source_type,
            prompt=prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create design")
        raise HTTPException(status_code=500, detail="Internal error creating design") from exc

    return {
        "id": design.id,
        "name": design.name,
        "source_type": design.source_type,
        "file_url": design.file_url,
        "width": design.width,
        "height": design.height,
    }


@router.get("")
def list_designs(
    source_type: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> dict:
    """List designs, optionally filtered by source_type.

    Returns:
        200 {designs: [...]}
    """
    designs = design_service.list_designs(db, source_type=source_type)
    return {
        "designs": [
            {
                "id": d.id,
                "name": d.name,
                "source_type": d.source_type,
                "file_url": d.file_url,
                "width": d.width,
                "height": d.height,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in designs
        ]
    }


@router.delete("/{design_id}", status_code=204)
def delete_design(
    design_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin_token),
) -> Response:
    """Delete a design by id (cascades R2 file + composite cache).

    Returns:
        204 No Content on success.
        404 if not found.
    """
    try:
        design_service.delete_design(db, design_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete design %d", design_id)
        raise HTTPException(status_code=500, detail="Internal error deleting design") from exc
    return Response(status_code=204)
