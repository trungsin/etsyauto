# Codebase Summary

Module-by-module index with line counts. Updated: 2026-05-05.

## Backend (`backend/`)

### Entry & Config

| File | LOC | Purpose |
|------|-----|---------|
| `app/main.py` | 77 | FastAPI app factory — lifespan (scheduler start/stop), CORS, static files, router registration |
| `app/config.py` | 43 | Pydantic `Settings` — loads all env vars from `.env`; singleton `settings` |
| `app/database.py` | 49 | SQLAlchemy engine + `SessionLocal` + `Base` + `get_db` dependency + `check_db()` |
| `app/scheduler.py` | 93 | APScheduler `BackgroundScheduler` singleton — registers 5 jobs, `start()`/`shutdown()` |

### Routes (`app/routes/`)

| File | LOC | Purpose |
|------|-----|---------|
| `routes/health.py` | 25 | `GET /health` — returns `{status, db, scheduler}` |
| `routes/ingest.py` | 95 | `POST /ingest` — idempotent listing ingestion from extension |
| `routes/etsy_auth.py` | 89 | `GET /auth/etsy/start|callback|status` — PKCE OAuth flow |
| `routes/admin.py` | 35 | `POST /admin/run-uploader` — manual job trigger, X-Admin-Token protected |

### Models (`app/models/`)

| File | LOC | Purpose |
|------|-----|---------|
| `models/listing.py` | 34 | `Listing` table — core state machine, etsy_listing_id, status, notion_page_id |
| `models/title_variant.py` | 20 | `TitleVariant` — 3 Claude-generated variants per listing |
| `models/mockup_variant.py` | 22 | `MockupVariant` — 3 Imagen-generated variants with R2 URLs |
| `models/job.py` | 21 | `Job` — async task execution tracking |
| `models/api_credential.py` | 17 | `ApiCredential` — Etsy OAuth tokens (access + refresh) |

### Workers (`app/workers/`)

| File | LOC | Purpose |
|------|-----|---------|
| `workers/title_optimizer.py` | 64 | Picks `status=new` listings → calls Etsy API to enrich → calls Claude → stores 3 variants → sets `mockup-pending` |
| `workers/mockup_pipeline.py` | 109 | Picks `status=mockup-pending` → remove.bg → Gemini Imagen ×3 → R2 upload → sets `review` |
| `workers/notion_sync.py` | 169 | `sync_to_notion`: creates Notion review page with title/mockup blocks. `pull_approvals`: polls Notion for Approved status → updates SQLite |
| `workers/etsy_uploader.py` | 174 | Picks `status=approved` → PATCH Etsy listing title → POST listing image → sets `pushed`; exponential backoff on failure |

### Services (`app/services/`)

| File | LOC | Purpose |
|------|-----|---------|
| `services/listing_service.py` | 199 | DB query helpers — fetch listings by status, update status, get variants |
| `services/image_service.py` | 127 | PIL utilities — resize, composite, format conversion for mockup pipeline |
| `services/review_service.py` | 107 | Notion review logic — build page blocks, parse approval response |
| `services/retry_policy.py` | 25 | Exponential backoff decorator + `should_retry(attempts)` helper |

### Clients (`app/clients/`)

| File | LOC | Purpose |
|------|-----|---------|
| `clients/claude_client.py` | 124 | Anthropic SDK wrapper — `generate_title_variants(title, desc, tags)` → `list[TitleVariantData]` |
| `clients/etsy_api_client.py` | 198 | Etsy Open API v3 — `get_listing()`, `update_listing_title()`, `upload_listing_image()` |
| `clients/etsy_oauth.py` | 145 | PKCE helpers — `generate_pkce_pair()`, `build_auth_url()`, `exchange_code()`, `get_valid_token()` |
| `clients/gemini_imagen_client.py` | 82 | Google GenAI SDK wrapper — `generate_mockup_image(prompt)` → base64 PNG |
| `clients/notion_client.py` | 243 | Notion API wrapper — `create_review_page()`, `query_approved_listings()`, `validate_database_schema()` |
| `clients/r2_storage_client.py` | 64 | boto3 S3-compatible client — `upload_image(key, data)` → public URL |
| `clients/removebg_client.py` | 57 | remove.bg REST client — `remove_background(image_url)` → PNG bytes |

### Prompts (`app/prompts/`)

| File | LOC | Purpose |
|------|-----|---------|
| *(prompt templates)* | — | Claude system/user prompt strings for title generation |

### Migrations (`alembic/versions/`)

| File | Purpose |
|------|---------|
| `0001_initial.py` | Base schema — listings, title_variants, mockup_variants, jobs, api_credentials |
| `aa88c2baf005_*.py` | Add `push_attempts`, `last_push_error` columns to listings |
| `e7d4a402ca7b_*.py` | Add `notion_page_id`, `final_image_url` columns to listings |

### Tests (`tests/`)

| File | Tests | Coverage Target |
|------|-------|----------------|
| `test_ingest.py` | ~12 | POST /ingest happy path, idempotency, terminal status reset |
| `test_title_optimizer.py` | ~14 | Title generation, Claude mock, status transitions |
| `test_mockup_pipeline.py` | ~14 | remove.bg mock, Imagen mock, R2 upload mock |
| `test_notion_sync.py` | ~14 | Page creation, approval polling, schema validation |
| `test_etsy_api_client.py` | ~12 | Listing fetch, title update, image upload |
| `test_etsy_uploader.py` | ~12 | Uploader job, retry logic, push_attempts increment |

**Total: 78 tests passing**

---

## Extension (`extension/`)

| File | LOC | Purpose |
|------|-----|---------|
| `manifest.json` | ~40 | MV3 manifest — permissions, host_permissions, background, side_panel, content_scripts |
| `background/service-worker.js` | ~120 | Handles messages from content script, calls backend API, manages auth state |
| `side-panel/side-panel.html` | ~60 | Side panel UI shell |
| `side-panel/side-panel.js` | ~150 | Side panel logic — display listing status, show variants, trigger ingest |
| `side-panel/side-panel.css` | ~80 | Side panel styles |
| `content-scripts/listing-detector.js` | ~80 | Detects Etsy listing page, extracts listing_id/title/images, sends to service worker |
| `content-scripts/listing-detector.css` | ~20 | Injected styles for detection indicators |
| `shared/` | ~60 | Shared constants and utility functions |

---

## Scripts (`scripts/`)

| File | Purpose |
|------|---------|
| `smoke-test-e2e.sh` | Structural readiness: uv sync → alembic → pytest → uvicorn /health → extension files |
| `seed-test-listing.py` | Insert 1 fake listing (etsy_listing_id=999999) into SQLite for manual debugging |

---

## Docs (`docs/`)

| File | Purpose |
|------|---------|
| `project-overview-pdr.md` | Problem statement, decisions, cost breakdown, tech stack |
| `system-architecture.md` | Component diagram, sequence diagram, state machine, DB ERD |
| `code-standards.md` | Python + JS standards, testing, git conventions |
| `codebase-summary.md` | This file — module index with LOC |
| `deployment-guide.md` | Step-by-step setup from scratch |
| `troubleshooting.md` | Common errors and fixes |
| `development-roadmap.md` | Phase history + post-MVP ideas |
| `project-changelog.md` | Version history |
| `notion-db-setup.md` | Notion database setup + review workflow |

---

## Key Dependency Versions

```toml
fastapi          = ">=0.136.1"
sqlalchemy       = ">=2.0.49"
alembic          = ">=1.18.4"
apscheduler      = ">=3.11.2"
anthropic        = ">=0.98.1"
google-genai     = ">=1.75.0"
boto3            = ">=1.43.3"
notion-client    = ">=3.0.0"
pydantic-settings= ">=2.14.0"
pillow           = ">=12.2.0"
httpx            = ">=0.28.1"
pytest           = ">=9.0.3"
```
