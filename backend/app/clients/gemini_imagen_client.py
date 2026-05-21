"""Gemini Imagen client — image-to-image scene replacement and artwork refinement via google-genai SDK."""
import base64
import logging

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL_ID = "gemini-2.5-flash-image"
_SCENE_INSTRUCTION = (
    "Place this product in the following scene: {scene}. "
    "Photorealistic, soft shadow, keep the product proportions intact. "
    "Do not alter the product itself."
)
_ARTWORK_REFINE_PROMPT = (
    "This is a design artwork cropped from a mockup image. "
    "Remove any mockup context (t-shirt texture, product shape, background clutter, shadows). "
    "Clean and sharpen the edges of the design. "
    "Output only the design on a perfectly clean white background, "
    "ready for background removal in the next step."
)


class GeminiImagenClient:
    """Wraps google-genai SDK for image-to-image scene replacement."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)

    def generate_scene(self, cutout_bytes: bytes, scene_prompt: str) -> bytes:
        """Generate a scene-replaced product image using Gemini image-to-image.

        Args:
            cutout_bytes: PNG bytes of the product with transparent background.
            scene_prompt: Short description of the target scene/environment.

        Returns:
            PNG bytes of the generated scene image.

        Raises:
            ValueError: if Gemini returns no image in the response.
            google.genai.errors.APIError: on API-level errors.
        """
        instruction = _SCENE_INSTRUCTION.format(scene=scene_prompt)
        image_b64 = base64.b64encode(cutout_bytes).decode("utf-8")

        response = self._client.models.generate_content(
            model=_MODEL_ID,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/png",
                                data=image_b64,
                            )
                        ),
                        types.Part(text=instruction),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract first image part from response
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.inline_data and part.inline_data.data:
                    image_bytes = base64.b64decode(part.inline_data.data)
                    logger.info(
                        "Gemini scene generated: %d bytes (scene=%r)",
                        len(image_bytes),
                        scene_prompt[:60],
                    )
                    return image_bytes

        raise ValueError(
            f"Gemini returned no image for scene prompt: {scene_prompt!r}. "
            f"Response: {response}"
        )

    def edit_for_artwork(self, image_bytes: bytes, prompt: str | None = None) -> bytes:
        """Refine a design artwork image using Gemini image editing.

        Sends the cropped mockup image with a cleaning prompt; returns a
        PNG with the design on a clean white background.

        Args:
            image_bytes: PNG bytes of the cropped design area.
            prompt: Override the default artwork refinement instruction.

        Returns:
            PNG bytes of the refined image.

        Raises:
            ValueError: If Gemini returns no image part in the response.
        """
        instruction = prompt or _ARTWORK_REFINE_PROMPT
        model = settings.gemini_image_edit_model or "gemini-2.0-flash-preview-image-generation"
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = self._client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/png",
                                data=image_b64,
                            )
                        ),
                        types.Part(text=instruction),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.inline_data and part.inline_data.data:
                    result_bytes = base64.b64decode(part.inline_data.data)
                    logger.info(
                        "Gemini artwork refine ok: %d → %d bytes (model=%s)",
                        len(image_bytes), len(result_bytes), model,
                    )
                    return result_bytes

        raise ValueError(
            f"Gemini returned no image for artwork refine. Model={model}. "
            f"Response text: {response.text!r}"
        )
