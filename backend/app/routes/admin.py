"""Admin endpoints — manual job triggers protected by X-Admin-Token header."""
import logging

from fastapi import APIRouter, Header, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_admin_token(x_admin_token: str | None) -> None:
    """Validate X-Admin-Token header; raises 401 if missing or incorrect."""
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured on server")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


@router.post("/run-uploader")
def trigger_uploader(
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Manually trigger the Etsy uploader job synchronously.

    Useful for debugging without waiting for the 10-minute cron interval.
    Requires X-Admin-Token header matching ADMIN_TOKEN env var.
    """
    _check_admin_token(x_admin_token)

    from app.workers.etsy_uploader import run_etsy_uploader_job  # local import avoids circular
    logger.info("admin: manually triggering etsy_uploader job")
    result = run_etsy_uploader_job()
    return {"status": "triggered", "result": result}
