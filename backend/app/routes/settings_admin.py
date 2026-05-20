"""Admin settings page — manage API keys and model settings at runtime.

- Remove.bg: unlimited key slots, per-key credit checker (existing keys checked server-side).
- Gemini: unlimited key slots, per-key connection test, rotation on 429.

Changes written to .env and applied in-memory (no restart needed).
Protected by X-Admin-Token / cookie / ?token= auth.
"""
from __future__ import annotations

import logging
import os

import httpx
from dotenv import set_key
from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

_ENV_FILE = ".env"
_REMOVEBG_ACCOUNT_URL = "https://api.remove.bg/v1.0/account"


def _check_token(
    x_admin_token: str | None,
    request: Request | None = None,
    admin_token_cookie: str | None = None,
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    candidates = [x_admin_token, admin_token_cookie]
    if request is not None:
        candidates.append(request.query_params.get("token"))
    if not any(c == settings.admin_token for c in candidates if c):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


def _get_jinja():
    from app.main import jinja_templates
    return jinja_templates


def _mask(value: str) -> str:
    if not value:
        return ""
    return "****" + value[-4:] if len(value) > 4 else "****"


def _current_removebg_keys() -> list[str]:
    if settings.removebg_api_keys:
        return [k.strip() for k in settings.removebg_api_keys.split(",") if k.strip()]
    keys = []
    if settings.removebg_api_key:
        keys.append(settings.removebg_api_key)
    if settings.removebg_api_key_backup:
        keys.append(settings.removebg_api_key_backup)
    return keys


def _current_gemini_keys() -> list[str]:
    if settings.gemini_api_keys:
        return [k.strip() for k in settings.gemini_api_keys.split(",") if k.strip()]
    if settings.gemini_api_key:
        return [settings.gemini_api_key]
    return []


async def _check_removebg_key(api_key: str) -> dict:
    """Call remove.bg account endpoint, return parsed credit info."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_REMOVEBG_ACCOUNT_URL, headers={"X-Api-Key": api_key})
    data = resp.json()
    if not resp.is_success:
        err = data.get("errors", [{}])[0].get("title", f"HTTP {resp.status_code}")
        return {"ok": False, "error": err}
    attrs = data.get("data", {}).get("attributes", {})
    credits = attrs.get("credits", {})
    api_info = attrs.get("api", {})
    return {
        "ok": True,
        "paid_total": credits.get("total", 0),
        "free_calls": api_info.get("free_calls", 0),
    }


# ---------------------------------------------------------------------------
# GET /admin/settings
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def settings_ui(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
    saved: str | None = None,
) -> HTMLResponse:
    _check_token(x_admin_token, request, admin_token)

    rbg_keys = _current_removebg_keys()
    gem_keys = _current_gemini_keys()

    return _get_jinja().TemplateResponse(
        request,
        "settings/settings.html",
        {
            "rbg_key_count": max(len(rbg_keys), 1),
            "rbg_key_masks": [_mask(k) for k in rbg_keys],
            "gem_key_count": max(len(gem_keys), 1),
            "gem_key_masks": [_mask(k) for k in gem_keys],
            "gemini_model": settings.gemini_ai_button_model,
            "saved": saved == "1",
        },
    )


# ---------------------------------------------------------------------------
# POST /admin/settings — save all fields
# ---------------------------------------------------------------------------


@router.post("", response_model=None)
async def save_settings(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> RedirectResponse:
    _check_token(x_admin_token, request, admin_token)

    form = await request.form()
    env_path = os.path.abspath(_ENV_FILE)

    # Remove.bg keys
    rbg_keys = [
        v.strip()
        for k, v in form.multi_items()
        if k == "rbg_key" and isinstance(v, str) and v.strip()
    ]
    if rbg_keys:
        joined = ",".join(rbg_keys)
        set_key(env_path, "REMOVEBG_API_KEYS", joined)
        settings.removebg_api_keys = joined
        settings.removebg_api_key = rbg_keys[0]
        settings.removebg_api_key_backup = rbg_keys[1] if len(rbg_keys) > 1 else ""
        logger.info("settings: updated REMOVEBG_API_KEYS (%d keys)", len(rbg_keys))

    # Gemini keys
    gem_keys = [
        v.strip()
        for k, v in form.multi_items()
        if k == "gem_key" and isinstance(v, str) and v.strip()
    ]
    if gem_keys:
        joined = ",".join(gem_keys)
        set_key(env_path, "GEMINI_API_KEYS", joined)
        set_key(env_path, "GEMINI_API_KEY", gem_keys[0])
        settings.gemini_api_keys = joined
        settings.gemini_api_key = gem_keys[0]
        logger.info("settings: updated GEMINI_API_KEYS (%d keys)", len(gem_keys))

    gemini_model = str(form.get("gemini_ai_button_model", "")).strip()
    if gemini_model:
        set_key(env_path, "GEMINI_AI_BUTTON_MODEL", gemini_model)
        settings.gemini_ai_button_model = gemini_model

    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# GET /admin/settings/removebg-credit/{idx} — check existing configured key by index
# ---------------------------------------------------------------------------


@router.get("/removebg-credit/{idx}")
async def check_removebg_credit_by_index(
    idx: int,
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Check credits for the Nth currently-configured remove.bg key (no key in request)."""
    _check_token(x_admin_token, request, admin_token)

    keys = _current_removebg_keys()
    if idx < 0 or idx >= len(keys):
        return JSONResponse({"ok": False, "error": "Key index out of range"}, status_code=404)

    try:
        return JSONResponse(await _check_removebg_key(keys[idx]))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# POST /admin/settings/removebg-credit — check an arbitrary (newly entered) key
# ---------------------------------------------------------------------------


@router.post("/removebg-credit")
async def check_removebg_credit_arbitrary(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Check credits for a key value passed in the request body (for unsaved keys)."""
    _check_token(x_admin_token, request, admin_token)

    body = await request.json()
    api_key = (body.get("key") or "").strip()
    if not api_key:
        return JSONResponse({"ok": False, "error": "No key provided"}, status_code=400)

    try:
        return JSONResponse(await _check_removebg_key(api_key))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# GET /admin/settings/gemini-test/{idx} — test existing key by index
# ---------------------------------------------------------------------------


@router.get("/gemini-test/{idx}")
def test_gemini_by_index(
    idx: int,
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Test Nth configured Gemini key."""
    _check_token(x_admin_token, request, admin_token)

    keys = _current_gemini_keys()
    if idx < 0 or idx >= len(keys):
        return JSONResponse({"ok": False, "error": "Key index out of range"}, status_code=404)

    return _run_gemini_test(keys[idx])


# ---------------------------------------------------------------------------
# POST /admin/settings/gemini-test — test arbitrary (newly entered) key
# ---------------------------------------------------------------------------


@router.post("/gemini-test")
async def test_gemini_arbitrary(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Test a Gemini key passed in the request body."""
    _check_token(x_admin_token, request, admin_token)

    body = await request.json()
    api_key = (body.get("key") or "").strip()
    if not api_key:
        return JSONResponse({"ok": False, "error": "No key provided"}, status_code=400)

    return _run_gemini_test(api_key)


def _run_gemini_test(api_key: str) -> JSONResponse:
    try:
        from app.clients.gemini_text_client import GeminiTextClient
        client = GeminiTextClient(api_keys=[api_key])
        result = client.generate_optimized_title(
            {"title": "Test product", "description": "A simple test"},
            model=settings.gemini_ai_button_model,
        )
        return JSONResponse({
            "ok": True,
            "model": settings.gemini_ai_button_model,
            "sample": result.get("text", "")[:80],
        })
    except Exception as exc:
        logger.exception("gemini test failed")
        err = str(exc)
        is_quota = "429" in err or "RESOURCE_EXHAUSTED" in err.upper()
        return JSONResponse({
            "ok": False,
            "error": "Quota/rate limit exceeded — try another key" if is_quota else err[:300],
            "is_quota": is_quota,
        })
