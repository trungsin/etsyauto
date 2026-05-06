"""FastAPI application entrypoint — wires up lifespan, routers, and static files."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import scheduler as sched
from app.config import settings
from app.routes.admin import router as admin_router
from app.routes.etsy_auth import router as etsy_auth_router
from app.routes.health import router as health_router
from app.routes.ingest import router as ingest_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _validate_notion_schema() -> None:
    """Best-effort Notion DB schema check on startup — logs warning, never blocks."""
    try:
        from app.clients.notion_client import NotionClient
        client = NotionClient()
        client.validate_database_schema()
    except ValueError:
        logger.warning(
            "Notion not configured (missing NOTION_API_KEY or NOTION_DATABASE_ID). "
            "Notion sync jobs will be no-ops until configured. "
            "See docs/notion-db-setup.md for setup instructions."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notion schema validation error (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate Notion schema, start scheduler. Shutdown: stop scheduler."""
    _validate_notion_schema()
    sched.start()
    yield
    sched.shutdown()


app = FastAPI(
    title="EtsyAuto",
    description="Listing optimizer backend — title generation, mockup pipeline, Etsy push.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: allow Chrome extension origins (chrome-extension://<id>) and localhost dev
# FastAPI CORSMiddleware does not natively support wildcard scheme matching, so we
# allow all origins whose prefix matches chrome-extension:// via allow_origin_regex.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8787"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Mount static files directory (create if missing)
static_path = Path(settings.static_dir)
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Routers
app.include_router(health_router)
app.include_router(etsy_auth_router)
app.include_router(ingest_router)
app.include_router(admin_router)
