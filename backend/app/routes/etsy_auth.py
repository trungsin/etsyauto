"""Etsy OAuth routes — /auth/etsy/start, /auth/etsy/callback, /auth/etsy/status."""
import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.clients.etsy_oauth import (
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    get_valid_token,
    save_token,
)
from app.database import get_db
from app.models.api_credential import ApiCredential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/etsy", tags=["auth"])

# In-memory PKCE state store: {state: code_verifier}
# Acceptable for single-process dev server; replace with Redis for multi-worker.
_pkce_store: dict[str, str] = {}


@router.get("/start")
def etsy_auth_start() -> RedirectResponse:
    """Generate PKCE pair, store verifier, redirect user to Etsy consent page."""
    state = secrets.token_urlsafe(16)
    verifier, challenge = generate_pkce_pair()
    _pkce_store[state] = verifier

    auth_url = build_auth_url(state=state, code_challenge=challenge)
    logger.info("etsy_auth_start: redirecting to Etsy, state=%s", state)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback")
def etsy_auth_callback(
    code: str = Query(..., description="Authorization code from Etsy"),
    state: str = Query(..., description="CSRF state token"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Receive Etsy redirect, exchange code for tokens, persist to DB."""
    verifier = _pkce_store.pop(state, None)
    if verifier is None:
        logger.warning("etsy_auth_callback: unknown or expired state=%s", state)
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    try:
        token = exchange_code(code=code, code_verifier=verifier)
    except Exception as exc:
        logger.error("etsy_auth_callback: token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc

    save_token(db, token)
    logger.info("etsy_auth_callback: token saved successfully")
    return JSONResponse(content={"status": "connected"})


@router.get("/status")
def etsy_auth_status(db: Session = Depends(get_db)) -> JSONResponse:
    """Report current token state: valid / expired / missing."""
    cred: ApiCredential | None = db.get(ApiCredential, "etsy")

    if cred is None or cred.oauth_token is None:
        return JSONResponse(content={"status": "missing"})

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = cred.expires_at

    if expires_at is None:
        token_state = "valid"
    elif expires_at < now:
        token_state = "expired"
    elif (expires_at - now).total_seconds() < 300:
        token_state = "expiring_soon"
    else:
        token_state = "valid"

    return JSONResponse(
        content={
            "status": token_state,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
    )
