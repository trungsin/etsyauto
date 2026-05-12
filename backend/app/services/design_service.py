"""Design service — CRUD for Design assets with R2 storage and composite cache invalidation."""
import logging
import uuid
from io import BytesIO

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.design import Design

logger = logging.getLogger(__name__)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_DIMENSION = 4000  # px — resize larger images before R2 upload


def _validate_png_with_alpha(file_bytes: bytes) -> Image.Image:
    """Validate PNG signature and presence of alpha channel.

    Args:
        file_bytes: Raw uploaded file bytes.

    Returns:
        Opened PIL image (RGBA) if valid.

    Raises:
        ValueError: If not PNG or no alpha channel.
    """
    if not file_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("Only PNG files are accepted")
    img = Image.open(BytesIO(file_bytes))
    if img.mode not in ("RGBA", "LA") and "transparency" not in img.info:
        raise ValueError("PNG must have an alpha channel (transparency)")
    return img.convert("RGBA")


def _resize_if_needed(img: Image.Image) -> Image.Image:
    """Resize image so max dimension ≤ _MAX_DIMENSION."""
    if max(img.width, img.height) <= _MAX_DIMENSION:
        return img
    if img.width >= img.height:
        new_w = _MAX_DIMENSION
        new_h = max(1, int(img.height * _MAX_DIMENSION / img.width))
    else:
        new_h = _MAX_DIMENSION
        new_w = max(1, int(img.width * _MAX_DIMENSION / img.height))
    logger.info("Resizing design from %dx%d to %dx%d", img.width, img.height, new_w, new_h)
    return img.resize((new_w, new_h), Image.LANCZOS)


def create_design(
    session: Session,
    file_bytes: bytes,
    name: str,
    source_type: str = "upload",
    prompt: str | None = None,
) -> Design:
    """Validate, resize, upload to R2, and persist a Design row.

    Args:
        session: Active SQLAlchemy session.
        file_bytes: Raw PNG bytes from upload.
        name: Display name for the design.
        source_type: 'upload', 'ai_generated', or 'reference_only'.
        prompt: Optional generation prompt (for ai_generated).

    Returns:
        Persisted Design instance.

    Raises:
        ValueError: On size > 10MB, invalid PNG, or missing alpha.
    """
    if len(file_bytes) > _MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds 10 MB limit ({len(file_bytes)} bytes)")

    img = _validate_png_with_alpha(file_bytes)
    img = _resize_if_needed(img)

    # Re-encode to PNG bytes after potential resize
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    final_bytes = buf.getvalue()

    key = f"designs/{uuid.uuid4().hex}.png"

    from app.clients.r2_storage_client import R2StorageClient
    r2 = R2StorageClient()
    public_url = r2.upload_image(final_bytes, key)

    design = Design(
        name=name,
        source_type=source_type,
        file_url=public_url,
        width=img.width,
        height=img.height,
        prompt_text=prompt,
    )
    session.add(design)
    session.commit()
    session.refresh(design)
    logger.info("Created design id=%d name=%s source_type=%s", design.id, name, source_type)
    return design


def list_designs(session: Session, source_type: str | None = None) -> list[Design]:
    """Return designs, optionally filtered by source_type, ordered by id desc."""
    stmt = select(Design).order_by(Design.id.desc())
    if source_type:
        stmt = stmt.where(Design.source_type == source_type)
    return list(session.scalars(stmt).all())


def get_design(session: Session, design_id: int) -> Design | None:
    """Return a single design by id, or None if not found."""
    return session.get(Design, design_id)


def update_design_file(session: Session, design_id: int, r2_key: str, file_url: str) -> None:
    """Overwrite the file_url on an existing Design row after R2 re-upload.

    Args:
        session: Active SQLAlchemy session.
        design_id: ID of the design to update.
        r2_key: New R2 object key (stored so caller can delete it later if needed).
        file_url: New public URL to set on design.file_url.

    Raises:
        ValueError: If design not found.
    """
    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")
    design.file_url = file_url
    # Store r2_key in file_url derivable form; the key IS the URL suffix so callers
    # can reconstruct it from settings.r2_public_url + "/" + r2_key.
    # We persist the full public URL; r2_key is kept by the caller for cleanup.
    session.commit()
    session.refresh(design)
    logger.info("Updated design id=%d file_url=%s", design_id, file_url)


def delete_design(session: Session, design_id: int) -> None:
    """Delete design DB row, R2 file, and all composite cache entries.

    Args:
        session: Active SQLAlchemy session.
        design_id: ID of design to delete.

    Raises:
        ValueError: If design not found.
    """
    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")

    file_url = design.file_url

    # Invalidate composite cache before DB delete
    try:
        from app.services import composite_service
        composite_service.invalidate_composites_for_design(design_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Composite cache invalidation failed for design %d: %s", design_id, exc)

    session.delete(design)
    session.commit()
    logger.info("Deleted design id=%d", design_id)

    # Best-effort R2 cleanup
    try:
        from app.clients.r2_storage_client import R2StorageClient
        from app.config import settings
        public_prefix = settings.r2_public_url.rstrip("/") + "/"
        if file_url.startswith(public_prefix):
            r2_key = file_url[len(public_prefix):]
            R2StorageClient().delete_object(r2_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("R2 delete failed for design %d: %s", design_id, exc)
