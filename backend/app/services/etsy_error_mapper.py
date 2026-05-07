"""Map Etsy ``httpx.HTTPStatusError`` exceptions to user-facing messages.

Keeps raw response body intact for logging — the mapper only adds a friendly
summary suitable for a UI toast. Routes use the returned (message, http_status)
to render to admin UI partials and JSON API errors consistently.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class MappedEtsyError:
    user_message: str
    http_status: int   # status to return from our API
    category: str      # short tag for logging / metrics


def map_etsy_error(exc: Exception) -> MappedEtsyError:
    """Translate an exception (typically ``httpx.HTTPStatusError``) into a
    UI-friendly message + HTTP status for our route to surface.

    Always returns a MappedEtsyError; non-Etsy errors fall under ``unknown``.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return MappedEtsyError(
            user_message=f"Unexpected error talking to Etsy: {exc}",
            http_status=502,
            category="unknown",
        )

    status = exc.response.status_code
    body = (exc.response.text or "").lower()

    if status == 401:
        return MappedEtsyError(
            user_message="Etsy login expired. Re-auth at /auth/etsy/start.",
            http_status=502,
            category="auth",
        )
    if status == 429:
        return MappedEtsyError(
            user_message="Etsy rate-limited. Try again in 30 seconds.",
            http_status=502,
            category="rate_limit",
        )
    if status == 400 and "image" in body:
        return MappedEtsyError(
            user_message="Image too small — Etsy requires at least 570×570.",
            http_status=422,
            category="image_size",
        )
    if status == 400 and ("taxonomy" in body or "value" in body):
        return MappedEtsyError(
            user_message="Color or size name doesn't match Etsy's palette. "
                         "Edit the template's variation_options to use valid names.",
            http_status=422,
            category="taxonomy",
        )
    if 500 <= status < 600:
        return MappedEtsyError(
            user_message="Etsy is having issues right now. Try again in a few minutes.",
            http_status=502,
            category="etsy_5xx",
        )

    # Generic fallthrough — keep the status code visible in the message
    return MappedEtsyError(
        user_message=f"Etsy returned {status}. Check server logs for details.",
        http_status=502,
        category="other",
    )
