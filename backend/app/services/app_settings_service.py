"""DB-backed settings service — read/write app_settings table.

Used by settings_admin to persist API keys to DB in addition to .env,
and by main.py startup to restore settings if .env is empty/wiped.
"""
import logging

from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

# Settings keys managed in DB (subset of config fields that users set via UI)
MANAGED_KEYS = {
    "REMOVEBG_API_KEY",
    "REMOVEBG_API_KEYS",
    "REMOVEBG_API_KEY_BACKUP",
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "GEMINI_AI_BUTTON_MODEL",
    "OPENAI_API_KEY",
}


def upsert(db: Session, key: str, value: str) -> None:
    """Insert or update a setting in the DB."""
    key = key.upper()
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()
    logger.debug("app_settings: upserted %s", key)


def get_all(db: Session) -> dict[str, str]:
    """Return all stored settings as a dict."""
    rows = db.query(AppSetting).all()
    return {r.key: r.value for r in rows}


def load_into_settings(db: Session) -> int:
    """Override empty/missing config fields from DB values. Returns count of applied overrides."""
    from app.config import settings  # local import to avoid circular
    rows = get_all(db)
    applied = 0
    for key, value in rows.items():
        attr = key.lower()
        if not value:
            continue
        current = getattr(settings, attr, None)
        if current is not None and not current:
            setattr(settings, attr, value)
            applied += 1
            logger.info("app_settings: restored %s from DB (was empty in .env)", key)
    return applied
