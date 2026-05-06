"""Health check endpoint — verifies app, DB, and scheduler are all live."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import check_db
from app.scheduler import is_running

router = APIRouter()


@router.get("/health", tags=["ops"])
def health_check() -> JSONResponse:
    """Return operational status of app, database, and scheduler."""
    db_ok = check_db()
    sched_ok = is_running()

    status_code = 200 if (db_ok and sched_ok) else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if status_code == 200 else "degraded",
            "db": "ok" if db_ok else "error",
            "scheduler": "running" if sched_ok else "stopped",
        },
    )
