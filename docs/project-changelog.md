# Project Changelog

All notable changes to EtsyAuto. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0] — 2026-05-05

Initial MVP release. Full pipeline from Chrome extension ingest to Etsy listing update with mandatory Notion approval gate.

### Added

#### Backend (FastAPI + SQLite)
- `app/main.py` — FastAPI app with CORS (chrome-extension://* + localhost), static file serving, APScheduler lifespan management
- `app/config.py` — Pydantic Settings loading all credentials from `backend/.env`
- `app/database.py` — SQLAlchemy 2.0 engine, session factory, `check_db()` health utility
- `app/scheduler.py` — APScheduler BackgroundScheduler singleton with 5 registered jobs

#### Routes
- `GET /health` — returns `{status, db, scheduler}`, 200/503
- `POST /ingest` — idempotent listing ingestion; accepts `{listing_id, source_url, title, images}` from extension
- `GET /auth/etsy/start` — initiates PKCE OAuth flow, redirects to Etsy consent
- `GET /auth/etsy/callback` — receives auth code, exchanges for tokens, persists to DB
- `GET /auth/etsy/status` — returns token validity and expiry
- `POST /admin/run-uploader` — manually triggers etsy_uploader job, protected by X-Admin-Token

#### Database Models (SQLAlchemy 2.0 mapped_column style)
- `Listing` — core state machine with status, etsy_listing_id, notion_page_id, push tracking
- `TitleVariant` — 3 Claude-generated title variants per listing with rationale + tags
- `MockupVariant` — 3 Imagen-generated mockup variants with R2 keys + public URLs
- `Job` — async task execution tracking with error_log
- `ApiCredential` — Etsy OAuth token storage (access + refresh + expiry)

#### Alembic Migrations
- `0001_initial` — full schema creation
- `aa88c2baf005` — add `push_attempts`, `last_push_error` to listings
- `e7d4a402ca7b` — add `notion_page_id`, `final_image_url` to listings

#### Workers (APScheduler jobs)
- `title_optimizer` (30s) — picks `status=new`, enriches from Etsy API, calls Claude Sonnet 4.6 for 3 title variants
- `mockup_pipeline` (60s) — picks `status=mockup-pending`, calls remove.bg → Gemini Imagen ×3 → uploads to Cloudflare R2
- `sync_to_notion` (30s) — creates Notion review page with title variant callout blocks + mockup image embeds
- `pull_approvals` (60s) — polls Notion for Approved status + selections, updates SQLite
- `etsy_uploader` (600s) — picks `status=approved`, PATCHes Etsy listing title + uploads mockup image; exponential backoff on failure

#### Clients
- `claude_client.py` — Anthropic SDK wrapper for `claude-sonnet-4-6`
- `etsy_api_client.py` — Etsy Open API v3 REST client (listing fetch, title update, image upload)
- `etsy_oauth.py` — PKCE pair generation, auth URL builder, token exchange, auto-refresh
- `gemini_imagen_client.py` — Google GenAI SDK wrapper for `gemini-2.5-flash-preview-05-20`
- `notion_client.py` — Notion API v1 client (DB query, page creation, schema validation)
- `r2_storage_client.py` — Cloudflare R2 upload via boto3 S3-compatible API
- `removebg_client.py` — remove.bg REST API client for background removal

#### Services
- `listing_service.py` — DB query helpers and status transition logic
- `image_service.py` — PIL utilities for image resize, composite, format conversion
- `review_service.py` — Notion page block construction and approval parsing
- `retry_policy.py` — exponential backoff decorator for external API calls

#### Chrome Extension (MV3)
- `manifest.json` — MV3 manifest, permissions: `activeTab`, `sidePanel`, `storage`; host_permissions: `etsy.com`, `localhost:8787`
- `background/service-worker.js` — message routing, backend API calls, OAuth state management
- `side-panel/` — HTML/JS/CSS side panel for listing display and ingest trigger
- `content-scripts/listing-detector.js` — Etsy listing page detector, extracts listing_id/title/images

#### Tests
- 78 unit tests across 6 test files
- Full mock coverage of all external APIs (Anthropic, Etsy, Notion, R2, remove.bg, Gemini)
- Tests: ingest idempotency, title generation, mockup pipeline, Notion sync/approval, Etsy upload + retry

#### Scripts
- `scripts/smoke-test-e2e.sh` — structural readiness check: uv sync, alembic, pytest, uvicorn /health, extension files
- `scripts/seed-test-listing.py` — inserts fake listing (etsy_listing_id=999999) for manual debugging

#### Documentation
- `README.md` — project overview, quickstart, architecture link, cost info
- `docs/project-overview-pdr.md` — problem statement, architecture decisions, cost breakdown
- `docs/system-architecture.md` — Mermaid component/sequence/state machine/ERD diagrams
- `docs/code-standards.md` — Python + JS standards, testing, git conventions
- `docs/codebase-summary.md` — module-by-module index with LOC counts
- `docs/deployment-guide.md` — full setup guide reproducible from scratch
- `docs/troubleshooting.md` — 9 common errors with diagnosis steps and fixes
- `docs/development-roadmap.md` — MVP phases + post-MVP backlog
- `docs/project-changelog.md` — this file
- `docs/notion-db-setup.md` — Notion DB creation, integration sharing, review workflow

### Architecture Decisions
- Local-first: SQLite + localhost FastAPI, no cloud infra required
- APScheduler over Celery — no Redis dependency for single-user MVP
- Notion as review UI — zero UI development, sellers already use Notion
- Cloudflare R2 for image hosting — free tier sufficient, Notion requires public HTTPS URLs
- Chrome MV3 extension — uses official Etsy page DOM, not scraping; compliant with ToS
- PKCE OAuth — no client_secret in browser, follows Etsy's recommended flow

### Known Limitations
- Single-user only (SQLite, in-memory PKCE state)
- Gemini model ID `gemini-2.5-flash-preview-05-20` is preview — switch to `gemini-2.5-flash-image` at GA
- No real E2E validation possible without live API keys and Etsy shop
- PKCE state lost on server restart (re-auth required)
