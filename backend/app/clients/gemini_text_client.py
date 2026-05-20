"""Gemini text client — generates SEO title variants via google-genai SDK."""
import logging
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "title_seo_prompt.md"
DESC_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "description_seo_prompt.md"
PROMPT_VERSION = "v1"
MODEL_ID = "gemini-2.5-flash"
MAX_TITLE_CHARS = 140
MAX_DESC_CHARS = 10000  # Etsy hard limit per description field

# Structured output schema for Gemini response_schema — enforces JSON shape
_TITLE_VARIANT_SCHEMA = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "char_count": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "target_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["text", "char_count", "rationale", "target_keywords"],
            },
        }
    },
    "required": ["variants"],
}


def _load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _truncate_to_limit(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    """Truncate at last comma before limit; fall back to hard slice."""
    if len(text) <= limit:
        return text
    candidate = text[:limit]
    last_comma = candidate.rfind(",")
    if last_comma > 0:
        return candidate[:last_comma].rstrip()
    return candidate.rstrip()


def _validate_variants(raw_variants: list[dict]) -> list[dict]:
    """Validate char limits and normalise char_count field."""
    validated: list[dict] = []
    for item in raw_variants:
        text = item.get("text", "")
        if not isinstance(text, str) or not text.strip():
            logger.warning("Skipping variant with empty text")
            continue
        text = text.strip()
        if len(text) > MAX_TITLE_CHARS:
            original_len = len(text)
            text = _truncate_to_limit(text)
            logger.warning(
                "Truncated variant from %d → %d chars", original_len, len(text)
            )
        item["text"] = text
        item["char_count"] = len(text)
        validated.append(item)
    return validated


_RATE_LIMIT_SIGNALS = ("429", "RESOURCE_EXHAUSTED", "quota")


def _is_rate_limited(exc: Exception) -> bool:
    s = str(exc).upper()
    return any(sig.upper() in s for sig in _RATE_LIMIT_SIGNALS)


class GeminiTextClient:
    """Wraps google-genai SDK for structured text generation with API key rotation.

    On 429 / RESOURCE_EXHAUSTED, automatically retries with the next configured key.
    Keys are read from GEMINI_API_KEYS (comma-separated) or the legacy GEMINI_API_KEY.
    """

    def __init__(self, api_keys: list[str] | None = None, prompt_path: Path | None = None) -> None:
        if api_keys is None:
            if settings.gemini_api_keys:
                api_keys = [k.strip() for k in settings.gemini_api_keys.split(",") if k.strip()]
            elif settings.gemini_api_key:
                api_keys = [settings.gemini_api_key]
        if not api_keys:
            raise ValueError("GEMINI_API_KEY is not configured")
        self._api_keys = api_keys
        self._prompt_path = prompt_path or PROMPT_PATH
        self._prompt_template = self._prompt_path.read_text(encoding="utf-8")

    def _call(self, model_id: str, prompt: str, config: types.GenerateContentConfig):
        """Call Gemini with automatic key rotation on rate-limit errors."""
        last_error: Exception | None = None
        for idx, key in enumerate(self._api_keys):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=model_id, contents=prompt, config=config
                )
                if idx > 0:
                    logger.info("Gemini succeeded with key #%d after rotation", idx)
                return response
            except Exception as exc:
                if _is_rate_limited(exc):
                    logger.warning("Gemini key #%d rate-limited — rotating to next key", idx)
                    last_error = exc
                    continue
                raise
        raise last_error  # type: ignore[misc]

    def generate_title_variants(self, listing_data: dict, model: str | None = None) -> list[dict]:
        """Call Gemini and return validated title variants.

        Args:
            listing_data: dict with keys original_title, description, tags, category.
            model: override model id (default: gemini-2.5-flash for background jobs).

        Returns:
            List of variant dicts: {text, char_count, rationale, target_keywords}.

        Raises:
            ValueError: if response yields no valid variants.
            google.genai.errors.APIError: on API-level errors.
        """
        # Support both prompt templates: old uses {description}, new reference
        # prompt uses {original_description}. Pass both so either template works.
        desc = listing_data.get("description") or listing_data.get("original_description", "")
        prompt = self._prompt_template.format(
            original_title=listing_data.get("original_title", ""),
            description=desc,
            original_description=listing_data.get("original_description") or desc,
            tags=listing_data.get("tags", ""),
            category=listing_data.get("category", ""),
            material=listing_data.get("material", ""),
        )

        model_id = model or MODEL_ID
        response = self._call(
            model_id,
            prompt,
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_TITLE_VARIANT_SCHEMA,
            ),
        )

        # Log token usage for cost tracking
        usage = response.usage_metadata
        if usage:
            logger.info(
                "Gemini token usage — input: %s, output: %s, model: %s",
                getattr(usage, "prompt_token_count", "?"),
                getattr(usage, "candidates_token_count", "?"),
                model_id,
            )

        import json  # noqa: PLC0415 — deferred to avoid top-level import ordering noise

        try:
            parsed = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"Gemini response is not valid JSON: {exc}\n{response.text!r}"
            ) from exc

        raw_variants = parsed.get("variants")
        if not isinstance(raw_variants, list):
            raise ValueError(f"Expected 'variants' list, got: {type(raw_variants)}")

        validated = _validate_variants(raw_variants)
        if not validated:
            raise ValueError("No valid variants after validation")

        return validated

    def generate_optimized_title(self, listing_data: dict, model: str | None = None) -> dict:
        """Return a single SEO-optimized title — reuses the variants prompt and picks variant #1.

        Args:
            listing_data: see generate_title_variants.
            model: override model id (admin AI button passes Pro).

        Returns: {"text": str, "char_count": int, "rationale": str, "target_keywords": list[str]}
        """
        variants = self.generate_title_variants(listing_data, model=model)
        return variants[0]

    def generate_optimized_description(self, listing_data: dict, model: str | None = None) -> dict:
        """Call Gemini with the description SEO prompt and return rewritten description.

        Args:
            listing_data: dict with keys original_title, original_description (or description),
                          tags, category.
            model: override model id (admin AI button passes Pro).

        Returns:
            {"text": str, "char_count": int}
        """
        prompt_template = DESC_PROMPT_PATH.read_text(encoding="utf-8")
        desc = listing_data.get("original_description") or listing_data.get("description") or ""
        prompt = prompt_template.format(
            original_title=listing_data.get("original_title", ""),
            original_description=desc,
            tags=listing_data.get("tags", ""),
            category=listing_data.get("category", ""),
        )

        schema = {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        }
        model_id = model or MODEL_ID
        response = self._call(
            model_id,
            prompt,
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        usage = response.usage_metadata
        if usage:
            logger.info(
                "Gemini description token usage — input: %s, output: %s, model: %s",
                getattr(usage, "prompt_token_count", "?"),
                getattr(usage, "candidates_token_count", "?"),
                model_id,
            )

        import json  # noqa: PLC0415
        try:
            parsed = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"Gemini description response is not valid JSON: {exc}\n{response.text!r}"
            ) from exc

        text = (parsed.get("description") or "").strip()
        if not text:
            raise ValueError("Gemini returned empty description")
        if len(text) > MAX_DESC_CHARS:
            text = text[:MAX_DESC_CHARS].rstrip()
        return {"text": text, "char_count": len(text)}
