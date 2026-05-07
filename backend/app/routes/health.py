"""Health check endpoint — verifies app, DB, scheduler, and reports flags."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import check_db
from app.scheduler import is_running

router = APIRouter()


@router.get("/health", tags=["ops"])
def health_check() -> JSONResponse:
    """Return operational status of app, database, and scheduler.

    Also surfaces ``etsy_dry_run`` flags so ops/admin UIs can warn when the
    server is intentionally short-circuiting Etsy calls (v0.7.0).
    """
    db_ok = check_db()
    sched_ok = is_running()

    status_code = 200 if (db_ok and sched_ok) else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if status_code == 200 else "degraded",
            "db": "ok" if db_ok else "error",
            "scheduler": "running" if sched_ok else "stopped",
            "etsy_dry_run": settings.etsy_dry_run,
            "etsy_dry_run_scenario": (
                settings.etsy_dry_run_scenario if settings.etsy_dry_run else None
            ),
        },
    )
