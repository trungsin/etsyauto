"""remove.bg API client — strips image background, returns transparent PNG bytes."""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_REMOVEBG_URL = "https://api.remove.bg/v1.0/removebg"


class RemoveBgClient:
    """Thin wrapper around the remove.bg REST API."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.removebg_api_key
        if not self._api_key:
            raise ValueError("REMOVEBG_API_KEY is not configured")

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """POST image bytes to remove.bg and return a transparent PNG.

        Args:
            image_bytes: Raw bytes of the source image (JPEG/PNG/WEBP).

        Returns:
            PNG bytes with background removed (RGBA, transparent BG).

        Raises:
            httpx.HTTPStatusError: on non-2xx response (e.g. quota exceeded).
            ValueError: if response body is unexpectedly empty.
        """
        with httpx.Client(timeout=60) as client:
            response = client.post(
                _REMOVEBG_URL,
                headers={"X-Api-Key": self._api_key},
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
