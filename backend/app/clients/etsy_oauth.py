"""Etsy OAuth 2.0 PKCE flow — code verifier/challenge, token exchange, refresh, and DB persistence."""
import base64
import hashlib
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.api_credential import ApiCredential

logger = logging.getLogger(__name__)

ETSY_AUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
PROVIDER = "etsy"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method.

    verifier: 32 random bytes → base64url (no padding)
    challenge: SHA-256(verifier) → base64url (no padding)
    """
    verifier_bytes = os.urandom(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(state: str, code_challenge: str) -> str:
    """Build Etsy authorization URL with PKCE parameters."""
    params = {
        "response_type": "code",
        "redirect_uri": settings.etsy_redirect_uri,
        "scope": settings.etsy_scope,
        "client_id": settings.etsy_api_key,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{ETSY_AUTH_URL}?{query}"


# ---------------------------------------------------------------------------
# Token exchange / refresh (sync — called from route handlers)
# ---------------------------------------------------------------------------

def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange authorization code + PKCE verifier for access/refresh tokens.

    Returns raw token dict from Etsy: {access_token, refresh_token, expires_in, ...}
    Raises httpx.HTTPStatusError on non-2xx.
    """
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.etsy_api_key,
        "redirect_uri": settings.etsy_redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }
    with httpx.Client() as client:
        resp = client.post(ETSY_TOKEN_URL, data=payload, timeout=15)
    resp.raise_for_status()
    token = resp.json()
    logger.info("exchange_code: received token, expires_in=%s", token.get("expires_in"))
    return token


def refresh_access_token(refresh_tok: str) -> dict:
    """Refresh an expired access token using the stored refresh token.

    Returns new token dict. Raises httpx.HTTPStatusError on failure.
    """
    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.etsy_api_key,
        "refresh_token": refresh_tok,
    }
    with httpx.Client() as client:
        resp = client.post(ETSY_TOKEN_URL, data=payload, timeout=15)
    resp.raise_for_status()
    token = resp.json()
    logger.info("refresh_access_token: token refreshed, expires_in=%s", token.get("expires_in"))
    return token


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

def save_token(db: Session, token: dict) -> None:
    """Upsert token dict into api_credentials for provider='etsy'."""
    expires_in = int(token.get("expires_in", 3600))
    expires_at = datetime.fromtimestamp(time.time() + expires_in, tz=timezone.utc).replace(tzinfo=None)

    cred = db.get(ApiCredential, PROVIDER)
    if cred is None:
        cred = ApiCredential(provider=PROVIDER)
        db.add(cred)

    cred.oauth_token = token["access_token"]
    cred.refresh_token = token.get("refresh_token", cred.refresh_token)
    cred.expires_at = expires_at
    db.commit()
    logger.info("save_token: persisted token, expires_at=%s", expires_at)


def get_valid_token(db: Session) -> str | None:
    """Return a valid access token, refreshing if it expires within 5 minutes.

    Returns None if no credential row exists yet.
    """
    cred = db.get(ApiCredential, PROVIDER)
    if cred is None or cred.oauth_token is None:
        return None

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = cred.expires_at

    # Refresh if missing expiry info OR within 5-minute window
    needs_refresh = (expires_at is None) or (
        (expires_at - now).total_seconds() < 300
    )

    if needs_refresh and cred.refresh_token:
        try:
            new_token = refresh_access_token(cred.refresh_token)
            save_token(db, new_token)
            # Re-fetch after commit
            db.refresh(cred)
        except Exception as exc:
            logger.error("get_valid_token: refresh failed: %s", exc)
            # Return the potentially stale token — caller will handle 401

    return cred.oauth_token
