"""Admin JSON/HTMX endpoints for the template image pool.

All routes require X-Admin-Token (checked via _check_token from templates_admin).
Prefix: /admin/templates/{template_id}/images
"""
import logging
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes.templates_admin import _check_token
from app.services import composite_service, template_image_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/templates/{template_id}/images",
    tags=["admin-template-images"],
)

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_MIME = {"image/png", "image/jpeg"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_upload(file: UploadFile, file_bytes: bytes) -> None:
    """Raise 413/415 for size or MIME violations."""
    if len(file_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MB limit")
    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type {content_type!r}. Must be image/png or image/jpeg.",
        )


def _r2_key_from_url(url: str) -> str | None:
    """Extract R2 object key from a public URL. Returns None on parse failure."""
    try:
        return urlparse(url).path.lstrip("/") or None
    except Exception:  # noqa: BLE001
        return None


def _invalidate_for_image(template_id: int, img_id: int) -> None:
    composite_service.invalidate_composites_for_image(img_id)


def _image_dict(v: template_image_service.TemplateImageView) -> dict:
    return {
        "id": v["id"],
        "image_url": v["image_url"],
        "color": v["color"],
        "rank": v["rank"],
        "role": v["role"],
        "anchor_json": v["anchor_json"],
        "is_virtual": v["is_virtual"],
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class _ImagePatch(BaseModel):
    """All fields optional — only provided fields are updated."""
    color: str | None = None
    rank: int | None = None
    role: str | None = None
    anchor_json: str | None = None
    image_url: str | None = None


class _ReorderItem(BaseModel):
    id: int
    rank: int


# ---------------------------------------------------------------------------
# POST "" — upload single image
# ---------------------------------------------------------------------------


@router.post("")
async def upload_image(
    template_id: int,
    file: UploadFile = File(...),
    color: str | None = Form(None),
    rank: int = Form(0),
    role: str = Form("mockup"),
    anchor_json: str = Form("{}"),
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_admin_token)

    file_bytes = await file.read()
    _validate_upload(file, file_bytes)

    filename = file.filename or "image.png"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    key = f"template-images/{template_id}/{uuid.uuid4().hex}.{ext}"

    from app.clients.r2_storage_client import R2StorageClient
    try:
        r2 = R2StorageClient()
        image_url = r2.upload_image(file_bytes, key)
    except Exception as exc:
        logger.exception("R2 upload failed for template %d", template_id)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    try:
        view = template_image_service.create(
            db,
            template_id=template_id,
            image_url=image_url,
            color=color,
            rank=rank,
            anchor_json=anchor_json,
            role=role,
        )
    except ValueError as exc:
        # Row creation failed — clean up the already-uploaded R2 object.
        try:
            r2.delete_object(key)
        except Exception:  # noqa: BLE001
            logger.warning("R2 cleanup after create failure failed: key=%s", key)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("Admin: uploaded template image id=%s template=%d", view["id"], template_id)
    return {
        "id": view["id"],
        "image_url": view["image_url"],
        "color": view["color"],
        "rank": view["rank"],
        "role": view["role"],
        "anchor_json": view["anchor_json"],
    }


# ---------------------------------------------------------------------------
# GET "" — list pool
# ---------------------------------------------------------------------------


@router.get("")
def list_images(
    template_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> list[dict]:
    _check_token(x_admin_token)
    return [_image_dict(v) for v in template_image_service.list_for_template(db, template_id)]


# ---------------------------------------------------------------------------
# PATCH "/{img_id}" — partial update
# ---------------------------------------------------------------------------


@router.patch("/{img_id}")
def patch_image(
    template_id: int,
    img_id: int,
    payload: _ImagePatch,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_admin_token)

    existing = template_image_service.get(db, img_id)
    if existing is None or existing["template_id"] != template_id:
        raise HTTPException(status_code=404, detail=f"Image {img_id} not found for template {template_id}")

    try:
        view = template_image_service.update(
            db,
            img_id,
            image_url=payload.image_url,
            color=payload.color,
            rank=payload.rank,
            anchor_json=payload.anchor_json,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _invalidate_for_image(template_id, img_id)
    logger.info("Admin: patched template image id=%d template=%d", img_id, template_id)
    return _image_dict(view)


# ---------------------------------------------------------------------------
# DELETE "/{img_id}" — delete image + R2 object
# ---------------------------------------------------------------------------


@router.delete("/{img_id}")
def delete_image(
    template_id: int,
    img_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_admin_token)

    existing = template_image_service.get(db, img_id)
    if existing is None or existing["template_id"] != template_id:
        raise HTTPException(status_code=404, detail=f"Image {img_id} not found for template {template_id}")

    # Best-effort R2 delete — only for keys in our managed prefix.
    r2_key = _r2_key_from_url(existing["image_url"])
    if r2_key and r2_key.startswith("template-images/"):
        try:
            from app.clients.r2_storage_client import R2StorageClient
            R2StorageClient().delete_object(r2_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 delete failed for key %s: %s", r2_key, exc)

    _invalidate_for_image(template_id, img_id)
    template_image_service.delete(db, img_id)
    logger.info("Admin: deleted template image id=%d template=%d", img_id, template_id)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# POST "/reorder" — bulk rank update
# ---------------------------------------------------------------------------


@router.post("/reorder")
def reorder_images(
    template_id: int,
    items: list[_ReorderItem],
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_admin_token)
    try:
        count = template_image_service.reorder(
            db, template_id, [{"id": it.id, "rank": it.rank} for it in items]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"reordered": count}


# ---------------------------------------------------------------------------
# POST "/migrate" — materialize legacy virtual rows
# ---------------------------------------------------------------------------


@router.post("/migrate")
def migrate_images(
    template_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_admin_token)
    count = template_image_service.materialize(db, template_id)
    return {"materialized": count}
