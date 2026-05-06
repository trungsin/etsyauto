"""Image service — download, resize, persist product images, and upload to R2."""
import io
import logging
import uuid
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MIN_DIMENSION_PX = 800
_MAX_MB = 4


def download_image(url: str) -> bytes:
    """Download image bytes from a URL, validating content-type.

    Raises:
        ValueError: if content-type is not an image or download fails.
        httpx.HTTPError: on network errors.
    """
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise ValueError(f"Unexpected content-type '{content_type}' for URL: {url}")

    return response.content


def resize_if_needed(image_bytes: bytes, max_mb: float = _MAX_MB) -> bytes:
    """Downscale image by halving dimensions until size < max_mb or min 800px.

    Args:
        image_bytes: Raw image bytes (any PIL-supported format).
        max_mb: Maximum allowed file size in MB.

    Returns:
        Resized PNG bytes (or original bytes if already within limit).
    """
    max_bytes = int(max_mb * 1024 * 1024)
    if len(image_bytes) <= max_bytes:
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    while True:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        candidate = buf.read()

        if len(candidate) <= max_bytes:
            return candidate

        w, h = img.size
        new_w, new_h = w // 2, h // 2

        if new_w < _MIN_DIMENSION_PX or new_h < _MIN_DIMENSION_PX:
            logger.warning(
                "Could not reduce below %dpx min dimension; returning best-effort %d bytes",
                _MIN_DIMENSION_PX,
                len(candidate),
            )
            return candidate

        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.debug("Resized image to %dx%d (%d bytes)", new_w, new_h, len(candidate))


def save_to_static(image_bytes: bytes, ext: str = "png") -> str:
    """Write bytes to STATIC_DIR/{uuid}.{ext} and return the relative path.

    Returns:
        Path string like 'static/abc123.png' (relative to project root).
    """
    static_dir = Path(settings.static_dir)
    static_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = static_dir / filename
    dest.write_bytes(image_bytes)
    logger.info("Saved image to %s (%d bytes)", dest, len(image_bytes))

    return str(dest)


def upload_to_r2(file_path: str) -> str | None:
    """Upload a local file to Cloudflare R2 and return the public URL.

    Returns None (and logs a warning) if R2 is not configured or upload fails.
    This is intentionally non-fatal — callers should treat None as "not yet uploaded".

    Args:
        file_path: Absolute or relative path to the file on disk.

    Returns:
        Public URL string, or None on failure / missing config.
    """
    from app.clients.r2_storage_client import R2StorageClient  # local import — optional dep

    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning("upload_to_r2: file not found: %s", file_path)
            return None

        file_bytes = path.read_bytes()
        ext = path.suffix or ".png"
        key = f"mockups/{uuid.uuid4().hex}{ext}"

        client = R2StorageClient()
        return client.upload_image(file_bytes, key)

    except ValueError as exc:
        # R2 not configured (missing env vars)
        logger.warning("upload_to_r2: R2 not configured — %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — upload must not crash the pipeline
        logger.warning("upload_to_r2: upload failed for %s — %s", file_path, exc)
        return None
