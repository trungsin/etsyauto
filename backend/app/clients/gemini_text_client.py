"""Gemini text client — generates SEO title variants via google-genai SDK."""
import logging
from pathlib import Path

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "title_seo_prompt.md"
PROMPT_VERSION = "v1"
MODEL_ID = "gemini-2.5-flash"
MAX_TITLE_CHARS = 140

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


class GeminiTextClient:
    """Wraps google-genai SDK for structured text generation (title variants)."""

    def __init__(self, api_key: str | None = None, prompt_path: Path | None = None) -> None:
        key = api_key or settings.gemini_api_key
        if not key:
            raise ValueError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)
        self._prompt_path = prompt_path or PROMPT_PATH
        self._prompt_template = self._prompt_path.read_text(encoding="utf-8")

    def generate_title_variants(self, listing_data: dict) -> list[dict]:
        """Call Gemini and return validated title variants.

        Args:
            listing_data: dict with keys original_title, description, tags, category.

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

        response = self._client.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
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
                MODEL_ID,
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
