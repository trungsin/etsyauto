"""Admin settings page — view and update runtime-configurable API keys and model settings.

Changes are written to the .env file immediately for persistence across restarts,
and applied to the in-memory settings singleton so they take effect without a restart.

Protected by the same X-Admin-Token / cookie / ?token= auth used across admin UI.
"""
from __future__ import annotations

import logging
import os

from dotenv import set_key
from fastapi import APIRouter, Cookie, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

# Path to the .env file (relative to wherever uvicorn is launched from — typically backend/)
_ENV_FILE = ".env"

# Fields exposed in the settings UI: (env_var_name, settings_attr, label, is_secret)
_FIELDS: list[tuple[str, str, str, bool]] = [
    ("REMOVEBG_API_KEY",        "removebg_api_key",        "Remove.bg API Key (Primary)",  True),
    ("REMOVEBG_API_KEY_BACKUP", "removebg_api_key_backup", "Remove.bg API Key (Backup)",   True),
    ("GEMINI_API_KEY",          "gemini_api_key",          "Gemini API Key",                True),
    ("GEMINI_AI_BUTTON_MODEL",  "gemini_ai_button_model",  "Gemini Model (AI buttons)",     False),
]


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
    """Show only the last 4 chars of a secret; blank if too short."""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


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
    """Render settings form with current values."""
    _check_token(x_admin_token, request, admin_token)

    fields = []
    for env_var, attr, label, is_secret in _FIELDS:
        current = getattr(settings, attr, "")
        fields.append({
            "env_var": env_var,
            "attr": attr,
            "label": label,
            "is_secret": is_secret,
            "display": _mask(current) if is_secret else current,
            "has_value": bool(current),
        })

    return _get_jinja().TemplateResponse(
        request,
        "settings/settings.html",
        {"fields": fields, "saved": saved == "1"},
    )


# ---------------------------------------------------------------------------
# POST /admin/settings
# ---------------------------------------------------------------------------


@router.post("", response_model=None)
def save_settings(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
    removebg_api_key: str = Form(default=""),
    removebg_api_key_backup: str = Form(default=""),
    gemini_api_key: str = Form(default=""),
    gemini_ai_button_model: str = Form(default=""),
) -> RedirectResponse:
    """Persist submitted settings to .env and apply to in-memory settings."""
    _check_token(x_admin_token, request, admin_token)

    updates: dict[str, tuple[str, str]] = {
        "removebg_api_key":        ("REMOVEBG_API_KEY",        removebg_api_key.strip()),
        "removebg_api_key_backup": ("REMOVEBG_API_KEY_BACKUP", removebg_api_key_backup.strip()),
        "gemini_api_key":          ("GEMINI_API_KEY",          gemini_api_key.strip()),
        "gemini_ai_button_model":  ("GEMINI_AI_BUTTON_MODEL",  gemini_ai_button_model.strip()),
    }

    env_path = os.path.abspath(_ENV_FILE)

    for attr, (env_var, value) in updates.items():
        if not value:
            continue  # empty submission = keep existing value
        # Write to .env
        set_key(env_path, env_var, value)
        # Apply to in-memory singleton immediately (no restart needed)
        setattr(settings, attr, value)
        logger.info("settings: updated %s", env_var)

    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)
