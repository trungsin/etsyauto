"""Pre-flight validation for the listing-creator pipeline.

Catches obvious failure modes (title length, tag count, combo count, composite
dimensions) BEFORE any Etsy API call so we don't waste quota on inputs Etsy
will reject anyway. Each issue has a `field` (so the route can map back to a
form field) and a human-readable `message`.
"""
from __future__ import annotations

from dataclasses import dataclass


# Etsy hard caps from public API docs (verified 2026-05-07)
TITLE_MAX_CHARS = 140
MAX_TAGS = 13
MAX_COMBOS = 30
MIN_IMAGE_DIM = 570  # px


@dataclass(frozen=True)
class Issue:
    field: str
    message: str


class PreCheckFailed(ValueError):
    """Raised when one or more pre-check issues are detected."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__(
            "; ".join(f"{i.field}: {i.message}" for i in issues)
            or "pre-check failed"
        )


def pre_check_listing(
    *,
    title: str,
    tags: list[str],
    enabled_combos: list[dict],
    composite_size: tuple[int, int] | None = None,
) -> list[Issue]:
    """Validate the listing inputs against Etsy's hard caps.

    Args:
        title: Listing title.
        tags: List of tag strings (post-trim).
        enabled_combos: List of {size, color, enabled} dicts.
        composite_size: Optional (width, height) of the rendered composite — when
            provided, enforce Etsy's 570×570 minimum.

    Returns:
        List of issues; empty list = OK to proceed to Etsy.
    """
    issues: list[Issue] = []

    if len(title) > TITLE_MAX_CHARS:
        issues.append(
            Issue(
                field="title",
                message=f"too long ({len(title)} chars > {TITLE_MAX_CHARS} cap)",
            )
        )

    if len(tags) > MAX_TAGS:
        issues.append(
            Issue(
                field="tags",
                message=f"too many ({len(tags)} > {MAX_TAGS} cap)",
            )
        )

    if len(enabled_combos) > MAX_COMBOS:
        issues.append(
            Issue(
                field="enabled_combos",
                message=f"too many ({len(enabled_combos)} > {MAX_COMBOS} Etsy cap)",
            )
        )

    if composite_size is not None:
        w, h = composite_size
        if w < MIN_IMAGE_DIM or h < MIN_IMAGE_DIM:
            issues.append(
                Issue(
                    field="composite",
                    message=f"too small ({w}×{h}, need ≥ {MIN_IMAGE_DIM}×{MIN_IMAGE_DIM})",
                )
            )

    return issues
