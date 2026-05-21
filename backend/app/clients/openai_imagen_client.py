"""OpenAI GPT-Image-1 client — image editing for the Cloden Design POD artwork pipeline.

Sends a cropped PNG to the /v1/images/edits endpoint with a cleaning prompt,
returns polished PNG bytes ready for remove.bg processing.
"""
import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_EDIT_URL = "https://api.openai.com/v1/images/edits"

# Default prompt for flat designs / mockup cutouts — removes mockup context,
# produces a clean white-background version ready for background removal.
_DEFAULT_PROMPT = (
    "Remove the mockup context (t-shirt texture, product shape, background clutter). "
    "Clean and smooth the edges of the design artwork. "
    "Output only the design on a clean white background, ready for background removal."
)


class OpenaiImagenClient:
    """Thin wrapper around the GPT-Image-1 /images/edits endpoint."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.openai_api_key
        if not key:
            raise ValueError("OPENAI_API_KEY is not configured")
        self._api_key = key

    def edit_image(
        self,
        image_bytes: bytes,
        prompt: str = _DEFAULT_PROMPT,
        size: str = "1024x1024",
    ) -> bytes:
        """POST PNG to GPT-Image-1 edit endpoint; return cleaned PNG bytes.

        Args:
            image_bytes: PNG bytes of the cropped design area.
            prompt: Instruction describing the cleanup to perform.
            size: Output dimensions — "1024x1024", "1024x1536", or "1536x1024".

        Returns:
            PNG bytes of the refined image (base64-decoded from response).

        Raises:
            ValueError: On API error, empty response, or unexpected response format.
            httpx.HTTPError: On network failure.
        """
        with httpx.Client(timeout=120) as client:
            response = client.post(
                _EDIT_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"image": ("image.png", image_bytes, "image/png")},
                data={
                    "model": settings.openai_image_model or "gpt-image-1",
                    "prompt": prompt,
                    "size": size,
                    "n": "1",
                },
            )
            response.raise_for_status()

        body = response.json()
        data = body.get("data", [])
        if not data or "b64_json" not in data[0]:
            raise ValueError(f"GPT-Image-1 returned no image data: {body}")

        png_bytes = base64.b64decode(data[0]["b64_json"])
        logger.info(
            "GPT-Image-1 edit success: input=%d bytes → output=%d bytes",
            len(image_bytes),
            len(png_bytes),
        )
        return png_bytes
