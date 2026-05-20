"""remove.bg API client — strips image background, returns transparent PNG bytes.

Key features:
- Auto-converts WEBP/JPEG/GIF → PNG before sending (remove.bg works best with PNG).
- Extracts human-readable error messages from remove.bg JSON error bodies.
- Rotates through API keys on 402 (quota) or 429 (rate limit).
"""
import logging
from io import BytesIO

import httpx
from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_REMOVEBG_URL = "https://api.remove.bg/v1.0/removebg"
_LIMIT_STATUS_CODES = {402, 429}


def _to_png(image_bytes: bytes) -> bytes:
    """Convert any PIL-readable image to PNG bytes (no-op if already PNG)."""
    # Fast path: already PNG (magic bytes \x89PNG)
    if image_bytes[:4] == b"\x89PNG":
        return image_bytes
    with Image.open(BytesIO(image_bytes)) as img:
        # Convert to RGBA so transparency is preserved
        img = img.convert("RGBA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        converted = buf.getvalue()
    logger.info("Converted image to PNG: %d → %d bytes", len(image_bytes), len(converted))
    return converted


def _extract_removebg_error(response: httpx.Response) -> str:
    """Extract the most informative error string from a remove.bg error response."""
    try:
        body = response.json()
        errors = body.get("errors", [])
        if errors:
            msgs = [e.get("title", "") for e in errors if e.get("title")]
            if msgs:
                return "; ".join(msgs)
    except Exception:
        pass
    return f"HTTP {response.status_code}"


class RemoveBgClient:
    """Thin wrapper around the remove.bg REST API with key-rotation on limit errors."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        if api_keys is None:
            if settings.removebg_api_keys:
                api_keys = [k.strip() for k in settings.removebg_api_keys.split(",") if k.strip()]
            else:
                api_keys = [k for k in [settings.removebg_api_key, settings.removebg_api_key_backup] if k]
        if not api_keys:
            raise ValueError("REMOVEBG_API_KEY is not configured")
        self._api_keys = api_keys

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """Convert image to PNG, POST to remove.bg, return transparent PNG bytes.

        Rotates to the next key on HTTP 402/429. Raises ValueError with the
        remove.bg error message on 400. Raises on all other failures.
        """
        png_bytes = _to_png(image_bytes)
        last_error: Exception | None = None

        for idx, api_key in enumerate(self._api_keys):
            try:
                result = self._call(api_key, png_bytes)
                if idx > 0:
                    logger.info("remove.bg succeeded with key #%d after rotation", idx)
                return result
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in _LIMIT_STATUS_CODES:
                    logger.warning("remove.bg key #%d hit limit (HTTP %d) — rotating", idx, code)
                    last_error = exc
                    continue
                # Surface the actual remove.bg error message for non-limit errors
                detail = _extract_removebg_error(exc.response)
                raise ValueError(f"Background removal failed: {detail}") from exc

        raise last_error  # type: ignore[misc]

    def _call(self, api_key: str, png_bytes: bytes) -> bytes:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                _REMOVEBG_URL,
                headers={"X-Api-Key": api_key},
                files={"image_file": ("image.png", png_bytes, "image/png")},
                data={"size": "auto", "type": "product", "crop": "true", "format": "png"},
            )
            response.raise_for_status()

        result = response.content
        if not result:
            raise ValueError("remove.bg returned empty response body")
        logger.info("remove.bg success: input=%d bytes → output=%d bytes", len(png_bytes), len(result))
        return result
