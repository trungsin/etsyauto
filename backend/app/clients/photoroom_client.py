"""PhotoRoom API client — strips image background, returns transparent PNG bytes.

Uses PhotoRoom's segment endpoint (https://sdk.photoroom.com/v1/segment).
Mirrors RemoveBgClient behaviour so it can drop into the same fallback chain:
- Auto-converts WEBP/JPEG/GIF → PNG before sending.
- Downsizes oversized PNGs (reuses remove.bg helpers — both APIs cap upload size).
- Extracts human-readable error messages from PhotoRoom JSON error bodies.
- Rotates through API keys on 402 (quota) / 429 (rate limit).
"""
import logging

import httpx

from app.clients.removebg_client import _fit_to_size_limit, _to_png
from app.config import settings

logger = logging.getLogger(__name__)

_PHOTOROOM_URL = "https://sdk.photoroom.com/v1/segment"
_LIMIT_STATUS_CODES = {402, 429}


def _extract_photoroom_error(response: httpx.Response) -> str:
    """Extract the most informative error string from a PhotoRoom error response."""
    try:
        body = response.json()
        # PhotoRoom shapes: {"error": {"message": ...}} or {"detail": ...}
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
        if isinstance(err, str) and err:
            return err
        if body.get("detail"):
            return str(body["detail"])
    except Exception:
        pass
    return f"HTTP {response.status_code}"


class PhotoRoomClient:
    """Thin wrapper around the PhotoRoom segment API with key-rotation on limit errors."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        if api_keys is None:
            if settings.photoroom_api_keys:
                api_keys = [k.strip() for k in settings.photoroom_api_keys.split(",") if k.strip()]
            elif settings.photoroom_api_key:
                api_keys = [settings.photoroom_api_key]
            else:
                api_keys = []
        if not api_keys:
            raise ValueError("PHOTOROOM_API_KEY is not configured")
        self._api_keys = api_keys

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """Convert image to PNG, POST to PhotoRoom, return transparent PNG bytes.

        Rotates to the next key on HTTP 402/429. Raises ValueError with the
        PhotoRoom error message on 400. Raises on all other failures.
        """
        png_bytes = _fit_to_size_limit(_to_png(image_bytes))
        last_error: Exception | None = None

        for idx, api_key in enumerate(self._api_keys):
            try:
                result = self._call(api_key, png_bytes)
                if idx > 0:
                    logger.info("PhotoRoom succeeded with key #%d after rotation", idx)
                return result
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code in _LIMIT_STATUS_CODES:
                    logger.warning("PhotoRoom key #%d hit limit (HTTP %d) — rotating", idx, code)
                    last_error = exc
                    continue
                detail = _extract_photoroom_error(exc.response)
                raise ValueError(f"Background removal failed: {detail}") from exc

        raise last_error  # type: ignore[misc]

    def _call(self, api_key: str, png_bytes: bytes) -> bytes:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                _PHOTOROOM_URL,
                headers={"x-api-key": api_key},
                files={"image_file": ("image.png", png_bytes, "image/png")},
                data={"format": "png"},
            )
            response.raise_for_status()

        result = response.content
        if not result:
            raise ValueError("PhotoRoom returned empty response body")
        logger.info("PhotoRoom success: input=%d bytes → output=%d bytes", len(png_bytes), len(result))
        return result
