"""Claude API wrapper — generates SEO title variants via Anthropic SDK."""
import json
import logging
import re
from pathlib import Path

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "title_seo_prompt.md"
PROMPT_VERSION = "v1"
MODEL_ID = "claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_TITLE_CHARS = 140


def _load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if Claude wraps JSON in them."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


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


class ClaudeClient:
    """Thin wrapper around anthropic.Anthropic for title variant generation."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._prompt_template = _load_prompt_template()

    def generate_title_variants(self, listing_data: dict) -> list[dict]:
        """Call Claude and return validated title variants.

        Args:
            listing_data: dict with keys original_title, description, tags, category.

        Returns:
            List of variant dicts: {text, char_count, rationale, target_keywords}.

        Raises:
            ValueError: if response cannot be parsed or yields no valid variants.
        """
        system_prompt = self._prompt_template.format(
            original_title=listing_data.get("original_title", ""),
            description=listing_data.get("description", ""),
            tags=listing_data.get("tags", ""),
            category=listing_data.get("category", ""),
        )

        response = self._client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "user", "content": "Generate the 3 title variants now."},
            ],
            system=system_prompt,
        )

        # Log token usage for cost tracking
        usage = response.usage
        logger.info(
            "Claude token usage — input: %d, output: %d, model: %s",
            usage.input_tokens,
            usage.output_tokens,
            MODEL_ID,
        )

        raw_text = response.content[0].text
        clean_text = _strip_code_fences(raw_text)

        try:
            parsed = json.loads(clean_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Claude response is not valid JSON: {exc}\n{clean_text}") from exc

        raw_variants = parsed.get("variants")
        if not isinstance(raw_variants, list):
            raise ValueError(f"Expected 'variants' list, got: {type(raw_variants)}")

        validated = _validate_variants(raw_variants)
        if not validated:
            raise ValueError("No valid variants after validation")

        return validated
