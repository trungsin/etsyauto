"""Correlation-ID middleware — assigns a per-request id reachable from anywhere
in the request handler chain (incl. EtsyApiClient log lines) via ContextVar.

The id is read from inbound ``X-Request-ID`` header if present (so callers can
thread their own ids), otherwise generated as a 12-char uuid hex. Either way it
is echoed back in the response header so admin UIs and curl users can capture
it for bug reports.
"""
from __future__ import annotations

import contextvars
import uuid
from typing import Awaitable, Callable

from fastapi import Request
from starlette.responses import Response

# Module-level ContextVar — async-safe, scoped to the in-flight request.
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


HEADER_NAME = "X-Request-ID"


def get_correlation_id() -> str:
    """Return the current request's correlation id, or '' outside a request."""
    return correlation_id_var.get()


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Set/echo X-Request-ID for every request."""
    cid = request.headers.get(HEADER_NAME) or uuid.uuid4().hex[:12]
    token = correlation_id_var.set(cid)
    try:
        response = await call_next(request)
        response.headers[HEADER_NAME] = cid
        return response
    finally:
        correlation_id_var.reset(token)
