"""FastAPI application entrypoint — wires up lifespan, routers, and static files."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import scheduler as sched
from app.config import settings
from app.routes.admin import router as admin_router
from app.routes.etsy_auth import router as etsy_auth_router
from app.routes.health import router as health_router
from app.routes.ingest import router as ingest_router
from app.routes.templates_api import router as templates_api_router
from app.routes.composite_api import router as composite_api_router
from app.routes.designs_api import router as designs_api_router
from app.routes.templates_admin import (
    auth_router as auth_admin_router,
    composite_router as composite_admin_router,
    creator_router as creator_admin_router,
    designs_router as designs_admin_router,
    router as templates_admin_router,
)
from app.routes.references_api import router as references_api_router
from app.routes.variations_api import router as variations_api_router
from app.routes.listings_creator_api import router as listings_creator_api_router

# Jinja2 templates — shared instance; imported by templates_admin.py lazily
_templates_dir = Path(__file__).parent / "templates"
jinja_templates = Jinja2Templates(directory=str(_templates_dir))

# Globals available to every template — banner reads etsy_dry_run flags
jinja_templates.env.globals["etsy_dry_run"] = settings.etsy_dry_run
jinja_templates.env.globals["etsy_dry_run_scenario"] = settings.etsy_dry_run_scenario

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _validate_notion_idea_bank_schema() -> None:
    """Best-effort validation of Notion Idea Bank DB on startup — logs warning, never blocks."""
    from app.config import settings as _s
    if not _s.notion_idea_bank_data_source_id:
        logger.info(
            "NOTION_IDEA_BANK_DATA_SOURCE_ID not set — Notion Idea Bank sync will be a no-op."
        )
        return
    try:
        from app.clients.notion_client import NotionClient
        client = NotionClient()
        client.validate_idea_bank_schema()
    except ValueError:
        logger.warning(
            "Notion Idea Bank not fully configured. "
            "Set NOTION_API_KEY + NOTION_IDEA_BANK_DATA_SOURCE_ID to enable sync."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notion Idea Bank schema validation error (non-fatal): %s", exc)


def _validate_notion_schema() -> None:
    """Best-effort Notion DB schema check on startup — logs warning, never blocks.

    Skips validation gracefully when NOTION_DATA_SOURCE_ID is not set (no-op,
    same behavior as missing API key — sync jobs will warn at runtime).
    """
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
    """Startup: validate Notion schemas, start scheduler. Shutdown: stop scheduler."""
    _validate_notion_schema()
    _validate_notion_idea_bank_schema()
    sched.start()
    yield
    sched.shutdown()


app = FastAPI(
    title="EtsyAuto",
    description="Listing optimizer backend — title generation, mockup pipeline, Etsy push.",
    version="0.1.0",
    lifespan=lifespan,
)


from app.middleware.correlation_id import correlation_id_middleware

app.middleware("http")(correlation_id_middleware)


@app.middleware("http")
async def admin_token_promote_middleware(request, call_next):
    """For /admin/* requests, promote the admin token from `?token=` query
    string or `admin_token` cookie into the `X-Admin-Token` header so all the
    existing route-level `_check_token` calls (which read only the header) keep
    working from a plain browser navigation. KISS: avoids touching every route.
    """
    path = request.url.path
    if path.startswith("/admin") and not request.headers.get("x-admin-token"):
        token = request.cookies.get("admin_token") or request.query_params.get("token")
        if token:
            new_headers = list(request.scope.get("headers", []))
            new_headers.append((b"x-admin-token", token.encode("latin-1")))
            request.scope["headers"] = new_headers
    return await call_next(request)

# CORS: allow Chrome extension origins (chrome-extension://<id>) and localhost dev
# FastAPI CORSMiddleware does not natively support wildcard scheme matching, so we
# allow all origins whose prefix matches chrome-extension:// via allow_origin_regex.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8787"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Mount static files directory (create if missing)
static_path = Path(settings.static_dir)
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Mount admin static files (CSS, JS for admin UI)
admin_static_path = Path(__file__).parent / "static"
admin_static_path.mkdir(parents=True, exist_ok=True)
app.mount("/admin/static", StaticFiles(directory=str(admin_static_path)), name="admin-static")

# Routers
app.include_router(health_router)
app.include_router(etsy_auth_router)
app.include_router(ingest_router)
app.include_router(admin_router)
app.include_router(templates_api_router)
app.include_router(variations_api_router)
app.include_router(designs_api_router)
app.include_router(composite_api_router)
app.include_router(templates_admin_router)
app.include_router(designs_admin_router)
app.include_router(composite_admin_router)
app.include_router(creator_admin_router)
app.include_router(auth_admin_router)
app.include_router(references_api_router)
app.include_router(listings_creator_api_router)
