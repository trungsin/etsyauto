"""remove.bg API client — strips image background, returns transparent PNG bytes.

Supports automatic API-key rotation: when the primary key hits its quota (402)
or rate limit (429), the client transparently retries with the next key in the
list. Raises the last error only after all keys are exhausted.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_REMOVEBG_URL = "https://api.remove.bg/v1.0/removebg"
# HTTP status codes that indicate a key-level limit (quota or rate)
_LIMIT_STATUS_CODES = {402, 429}


class RemoveBgClient:
    """Thin wrapper around the remove.bg REST API with key-rotation on limit errors."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        if api_keys is None:
            if settings.removebg_api_keys:
                # Multi-key list takes precedence (managed via /admin/settings)
                api_keys = [k.strip() for k in settings.removebg_api_keys.split(",") if k.strip()]
            else:
                # Legacy: primary + optional backup
                api_keys = [k for k in [settings.removebg_api_key, settings.removebg_api_key_backup] if k]
        if not api_keys:
            raise ValueError("REMOVEBG_API_KEY is not configured")
        self._api_keys = api_keys

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """POST image bytes to remove.bg and return a transparent PNG.

        Tries each configured API key in order. Rotates to the next key on
        HTTP 402 (quota exceeded) or 429 (rate limit). Raises on all other
        errors or when all keys are exhausted.

        Args:
            image_bytes: Raw bytes of the source image (JPEG/PNG/WEBP).

        Returns:
            PNG bytes with background removed (RGBA, transparent BG).

        Raises:
            httpx.HTTPStatusError: if all keys fail.
            ValueError: if response body is unexpectedly empty.
        """
        last_error: Exception | None = None

        for idx, api_key in enumerate(self._api_keys):
            try:
                result = self._call(api_key, image_bytes)
                if idx > 0:
                    logger.info("remove.bg succeeded with backup key #%d", idx)
                return result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _LIMIT_STATUS_CODES:
                    logger.warning(
                        "remove.bg key #%d hit limit (HTTP %d) — rotating to next key",
                        idx,
                        exc.response.status_code,
                    )
                    last_error = exc
                    continue
                raise  # non-limit errors propagate immediately

        raise last_error  # type: ignore[misc]  # always set when loop exhausted

    def _call(self, api_key: str, image_bytes: bytes) -> bytes:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                _REMOVEBG_URL,
                headers={"X-Api-Key": api_key},
                files={"image_file": ("image.png", image_bytes, "image/png")},
                data={
                    "size": "auto",
                    "type": "product",
                    "crop": "true",
                    "format": "png",
                },
            )
            response.raise_for_status()

        result = response.content
        if not result:
            raise ValueError("remove.bg returned empty response body")

        logger.info(
            "remove.bg success: input=%d bytes → output=%d bytes",
            len(image_bytes),
            len(result),
        )
        return result
