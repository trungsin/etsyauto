"""Admin settings page — manage API keys and model settings at runtime.

- Remove.bg: unlimited key slots, per-key credit checker.
- Gemini: key + model, live connection test.

Changes written to .env and applied in-memory (no restart needed).
Protected by X-Admin-Token / cookie / ?token= auth.
"""
from __future__ import annotations

import logging
import os

import httpx
from dotenv import set_key
from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
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
    """Return ordered list of configured remove.bg keys."""
    if settings.removebg_api_keys:
        return [k.strip() for k in settings.removebg_api_keys.split(",") if k.strip()]
    keys = []
    if settings.removebg_api_key:
        keys.append(settings.removebg_api_key)
    if settings.removebg_api_key_backup:
        keys.append(settings.removebg_api_key_backup)
    return keys


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

    return _get_jinja().TemplateResponse(
        request,
        "settings/settings.html",
        {
            "rbg_keys": rbg_keys,
            "rbg_key_masks": [_mask(k) for k in rbg_keys],
            "gemini_api_key_mask": _mask(settings.gemini_api_key),
            "gemini_api_key_set": bool(settings.gemini_api_key),
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

    # Remove.bg keys: collect all rbg_key_* fields, filter blanks
    rbg_keys = [
        v.strip()
        for k, v in form.multi_items()
        if k == "rbg_key" and isinstance(v, str) and v.strip()
    ]

    if rbg_keys:
        joined = ",".join(rbg_keys)
        set_key(env_path, "REMOVEBG_API_KEYS", joined)
        settings.removebg_api_keys = joined
        # Clear legacy fields so client picks up the new list
        settings.removebg_api_key = rbg_keys[0]
        settings.removebg_api_key_backup = rbg_keys[1] if len(rbg_keys) > 1 else ""
        logger.info("settings: updated REMOVEBG_API_KEYS (%d keys)", len(rbg_keys))

    gemini_key = str(form.get("gemini_api_key", "")).strip()
    if gemini_key:
        set_key(env_path, "GEMINI_API_KEY", gemini_key)
        settings.gemini_api_key = gemini_key
        logger.info("settings: updated GEMINI_API_KEY")

    gemini_model = str(form.get("gemini_ai_button_model", "")).strip()
    if gemini_model:
        set_key(env_path, "GEMINI_AI_BUTTON_MODEL", gemini_model)
        settings.gemini_ai_button_model = gemini_model
        logger.info("settings: updated GEMINI_AI_BUTTON_MODEL=%s", gemini_model)

    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/settings/removebg-credit — check credits for one key
# ---------------------------------------------------------------------------


@router.post("/removebg-credit")
async def check_removebg_credit(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Check remaining credits for a remove.bg API key."""
    _check_token(x_admin_token, request, admin_token)

    body = await request.json()
    api_key = (body.get("key") or "").strip()
    if not api_key:
        return JSONResponse({"ok": False, "error": "No key provided"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _REMOVEBG_ACCOUNT_URL,
                headers={"X-Api-Key": api_key},
            )
        data = resp.json()
        if not resp.is_success:
            err = data.get("errors", [{}])[0].get("title", f"HTTP {resp.status_code}")
            return JSONResponse({"ok": False, "error": err})

        attrs = data.get("data", {}).get("attributes", {})
        credits = attrs.get("credits", {})
        api_info = attrs.get("api", {})
        return JSONResponse({
            "ok": True,
            "paid_total": credits.get("total", 0),
            "free_calls": api_info.get("free_calls", 0),
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# GET /admin/settings/gemini-test — verify Gemini key is working
# ---------------------------------------------------------------------------


@router.get("/gemini-test")
def test_gemini(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Quick smoke-test: generate a tiny title with current Gemini config."""
    _check_token(x_admin_token, request, admin_token)

    if not settings.gemini_api_key:
        return JSONResponse({"ok": False, "error": "GEMINI_API_KEY not set"})

    try:
        from app.clients.gemini_text_client import GeminiTextClient
        client = GeminiTextClient()
        result = client.generate_optimized_title(
            {"title": "Test product", "description": "A simple test"},
            model=settings.gemini_ai_button_model,
        )
        return JSONResponse({"ok": True, "model": settings.gemini_ai_button_model, "sample": result.get("text", "")[:80]})
    except Exception as exc:
        logger.exception("gemini test failed")
        return JSONResponse({"ok": False, "error": str(exc)[:300]})
