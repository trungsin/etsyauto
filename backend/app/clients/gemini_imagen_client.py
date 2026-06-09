"""Gemini Imagen client — image-to-image scene replacement and artwork refinement via google-genai SDK."""
import base64
import logging

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL_ID = "gemini-2.5-flash-image"
# Fallback model when primary hits billing/quota errors.
_MODEL_FALLBACK = "gemini-3.1-flash-image-preview"
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

_QUOTA_SIGNALS = ("429", "resource_exhausted", "quota", "billing", "limit")
# Model not found → also worth trying fallback model (model deprecated or renamed).
_MODEL_NOT_FOUND_SIGNALS = ("404", "not_found", "is not found for api version")


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(sig in s for sig in _QUOTA_SIGNALS)


def _should_try_fallback(exc: Exception) -> bool:
    """True if we should try the fallback model (quota exhausted or model not found)."""
    s = str(exc).lower()
    return any(sig in s for sig in _QUOTA_SIGNALS) or any(sig in s for sig in _MODEL_NOT_FOUND_SIGNALS)


class GeminiImagenClient:
    """Wraps google-genai SDK for image-to-image scene replacement.

    Supports API key rotation: on quota/rate-limit errors, automatically retries
    with the next key from GEMINI_API_KEYS (comma-separated). Also falls back to
    _MODEL_FALLBACK when the primary model hits billing/quota errors.
    """

    def __init__(self, api_key: str | None = None) -> None:
        if api_key:
            self._api_keys = [api_key]
        elif settings.gemini_api_keys:
            self._api_keys = [k.strip() for k in settings.gemini_api_keys.split(",") if k.strip()]
        elif settings.gemini_api_key:
            self._api_keys = [settings.gemini_api_key]
        else:
            raise ValueError("GEMINI_API_KEY is not configured")

    def _generate_with_rotation(self, model: str, contents, config) -> types.GenerateContentResponse:
        """Call Gemini with automatic key rotation on quota errors."""
        last_error: Exception | None = None
        for idx, key in enumerate(self._api_keys):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(model=model, contents=contents, config=config)
                if idx > 0:
                    logger.info("Gemini image key #%d succeeded after rotation", idx)
                return response
            except Exception as exc:
                if _is_quota_error(exc):
                    logger.warning("Gemini image key #%d quota/rate error — rotating: %s", idx, str(exc)[:120])
                    last_error = exc
                    continue
                raise
        raise last_error  # type: ignore[misc]

    def _extract_image(self, response) -> bytes | None:
        """Extract first image bytes from a Gemini response, or None if absent."""
        for candidate in response.candidates or []:
            for part in (candidate.content.parts or []):
                if part.inline_data and part.inline_data.data:
                    return base64.b64decode(part.inline_data.data)
        return None

    def _build_contents(self, image_b64: str, instruction: str) -> list:
        return [
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type="image/png", data=image_b64)),
                    types.Part(text=instruction),
                ],
            )
        ]

    def generate_scene(self, cutout_bytes: bytes, scene_prompt: str) -> bytes:
        """Generate a scene-replaced product image using Gemini image-to-image."""
        instruction = _SCENE_INSTRUCTION.format(scene=scene_prompt)
        image_b64 = base64.b64encode(cutout_bytes).decode("utf-8")
        contents = self._build_contents(image_b64, instruction)
        config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

        response = self._generate_with_rotation(_MODEL_ID, contents, config)
        result = self._extract_image(response)
        if result is None:
            raise ValueError(
                f"Gemini returned no image for scene prompt: {scene_prompt!r}. "
                f"Response: {response}"
            )
        logger.info("Gemini scene generated: %d bytes (scene=%r)", len(result), scene_prompt[:60])
        return result

    def edit_for_artwork(self, image_bytes: bytes, prompt: str | None = None) -> bytes:
        """Refine a design artwork image with key rotation and model fallback.

        Tries the configured model first (settings.gemini_image_edit_model).
        On quota/billing errors, falls back to _MODEL_FALLBACK which has a free tier.
        Also rotates through all configured API keys on quota errors.

        Raises:
            ValueError: If all keys and models exhausted without returning an image.
        """
        instruction = prompt or _ARTWORK_REFINE_PROMPT
        primary_model = settings.gemini_image_edit_model or _MODEL_FALLBACK
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        contents = self._build_contents(image_b64, instruction)
        config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

        # Try primary model with key rotation
        last_error: Exception | None = None
        try:
            response = self._generate_with_rotation(primary_model, contents, config)
            result = self._extract_image(response)
            if result is not None:
                logger.info("Gemini artwork refine ok: %d → %d bytes (model=%s)", len(image_bytes), len(result), primary_model)
                return result
            raise ValueError(f"Gemini returned no image. Model={primary_model}. Response: {getattr(response, 'text', '')!r}")
        except Exception as exc:
            last_error = exc
            if primary_model == _MODEL_FALLBACK:
                raise
            if not _should_try_fallback(exc):
                raise
            logger.warning("Gemini primary model %s failed — trying fallback %s: %s", primary_model, _MODEL_FALLBACK, str(exc)[:120])

        # Fallback model with key rotation
        response = self._generate_with_rotation(_MODEL_FALLBACK, contents, config)
        result = self._extract_image(response)
        if result is not None:
            logger.info("Gemini artwork refine ok (fallback): %d → %d bytes (model=%s)", len(image_bytes), len(result), _MODEL_FALLBACK)
            return result
        raise ValueError(f"Gemini returned no image on fallback model either. Last error: {last_error}")
