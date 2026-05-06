"""Shared FastAPI dependencies used across multiple routers."""
from fastapi import Header, HTTPException

from app.config import settings


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    """Dependency: validate X-Admin-Token header.

    Raises:
        HTTPException 503: Admin token not configured on server.
        HTTPException 401: Missing or invalid token.
    """
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured on server")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")
