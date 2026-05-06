"""Image generation client — text-to-image background generation via google-genai SDK.

Strategy: tries Imagen-4 first (paid tier, best quality), then falls back to
Nano Banana models (gemini-2.5-flash-image / gemini-3.1-flash-image-preview)
which use generate_content with IMAGE modality and may have free-tier quota.

All models return PNG bytes of a background scene (no product).
"""
import base64
import logging

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

# Primary: Imagen-4 via generate_images (paid tier)
_IMAGEN_MODEL_ID = "imagen-4.0-fast-generate-001"

# Fallbacks: Nano Banana — use generate_content with IMAGE modality
_NANO_BANANA_MODELS = [
    "gemini-2.5-flash-image",          # Nano Banana
    "gemini-3.1-flash-image-preview",  # Nano Banana 2
    "nano-banana-pro-preview",         # Nano Banana Pro
]


class ImagenClient:
    """Wraps google-genai SDK for text-to-image background scene generation.

    Tries Imagen-4 (generate_images) first; if quota/billing error falls back
    to Nano Banana models (generate_content with IMAGE modality).
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)

    def generate_background(
        self,
        scene_prompt: str,
        width: int = 1024,
        height: int = 1024,
    ) -> bytes:
        """Generate a background scene image with no product.

        Tries Imagen-4 first, then Nano Banana fallbacks.

        Args:
            scene_prompt: Description of the background scene (no product).
            width: Desired output width in pixels (default 1024).
            height: Desired output height in pixels (default 1024).

        Returns:
            PNG bytes of the generated background scene.

        Raises:
            RuntimeError: if all models fail.
        """
        # --- attempt 1: Imagen-4 via generate_images ---
        try:
            return self._generate_via_imagen(scene_prompt, width, height)
        except Exception as exc:
            logger.warning(
                "Imagen-4 failed (%s: %s) — trying Nano Banana fallbacks",
                type(exc).__name__,
                str(exc)[:120],
            )

        # --- attempt 2: Nano Banana models via generate_content ---
        last_exc: Exception | None = None
        for model_id in _NANO_BANANA_MODELS:
            try:
                return self._generate_via_generate_content(model_id, scene_prompt)
            except Exception as exc:
                logger.warning(
                    "Model %s failed (%s: %s)",
                    model_id,
                    type(exc).__name__,
                    str(exc)[:120],
                )
                last_exc = exc

        raise RuntimeError(
            f"All image generation models failed for prompt {scene_prompt!r}. "
            f"Last error: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate_via_imagen(self, scene_prompt: str, width: int, height: int) -> bytes:
        """Generate via Imagen-4 generate_images API."""
        aspect_ratio = "1:1"
        if width > height:
            aspect_ratio = "4:3"
        elif height > width:
            aspect_ratio = "3:4"

        response = self._client.models.generate_images(
            model=_IMAGEN_MODEL_ID,
            prompt=scene_prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            ),
        )

        generated = response.generated_images
        if not generated:
            raise ValueError(f"Imagen returned no images for prompt: {scene_prompt!r}")

        image_bytes = generated[0].image.image_bytes
        if not image_bytes:
            raise ValueError(f"Imagen returned empty bytes for prompt: {scene_prompt!r}")

        logger.info(
            "Imagen-4 background generated: %d bytes (prompt=%r)",
            len(image_bytes),
            scene_prompt[:60],
        )
        return image_bytes

    def _generate_via_generate_content(self, model_id: str, scene_prompt: str) -> bytes:
        """Generate via Nano Banana generate_content with IMAGE modality."""
        response = self._client.models.generate_content(
            model=model_id,
            contents=scene_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        for candidate in response.candidates or []:
            for part in (candidate.content.parts or []):
                if part.inline_data and part.inline_data.data:
                    raw = part.inline_data.data
                    # SDK may return base64 string or raw bytes
                    image_bytes = base64.b64decode(raw) if isinstance(raw, str) else raw
                    logger.info(
                        "Nano Banana (%s) background generated: %d bytes (prompt=%r)",
                        model_id,
                        len(image_bytes),
                        scene_prompt[:60],
                    )
                    return image_bytes

        raise ValueError(
            f"Model {model_id!r} returned no image for prompt: {scene_prompt!r}. "
            f"Response: {response}"
        )
