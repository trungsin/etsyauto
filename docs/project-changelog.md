# Project Changelog

All notable changes to EtsyAuto. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.2.0] — 2026-05-06

Template System & Mockup Composer (Sub-feature B). POD-style template management with Pillow alpha-composite preview, variations matrix, design library, and Jinja2+HTMX admin UI.

### Added

#### Database Models
- `Template` — product blank image with composite anchor (x/y/w/h fractions), variation_options JSON, default price, R2 URL
- `TemplateVariation` — size/color/price_cents/sku row; max 30 per template (Etsy hard limit); unique (size, color) constraint
- `Design` — uploaded artwork PNG; `source_type` ∈ {upload, ai_generated, reference_only}; `reference_only` excluded from composite

#### Alembic Migration
- `240d6e765e57_template_design_tables` — creates `templates`, `template_variations`, `designs` tables

#### Routes — JSON API
- `GET/POST /templates` — list all templates, create new template (multipart form + base image upload)
- `GET/PUT/DELETE /templates/{id}` — fetch, update, or delete template; update invalidates composite cache
- `GET/POST /templates/{id}/variations` — list or bulk-replace variations (atomic: delete-all + insert)
- `PUT /templates/{id}/variations/{vid}` — update single variation price/SKU
- `DELETE /templates/{id}/variations` — clear all variations for a template
- `GET/POST /designs` — list designs (with source_type filter), upload RGBA PNG design
- `GET/DELETE /designs/{id}` — fetch or delete design (cascades composite cache invalidation)
- `POST /composite/preview` — trigger Pillow composite; returns `{composite_url, cached}` where `cached=true` means R2 hit

#### Routes — Admin UI (Jinja2 + HTMX)
- `GET /admin/templates` — template list page with HTMX delete confirmation
- `GET /admin/templates/new` — new template upload form
- `GET /admin/templates/{id}` — template detail: image preview, anchor display, variations matrix, composite preview
- `GET /admin/templates/{id}/edit` — edit template metadata form
- `POST /admin/templates/{id}/delete` — delete with HTMX swap
- `GET /admin/templates/{id}/composite` — composite preview page (select design, show result inline)

#### Services
- `image_composite.py` — `composite_with_anchor(base_bytes, design_bytes, anchor)`: resize design to anchor region (LANCZOS), alpha_composite onto base; clamps out-of-range anchor values
- `template_service.py` — template CRUD with R2 cleanup on delete, composite cache invalidation (list + delete R2 keys) on update
- `variation_service.py` — bulk_replace (atomic), list, update_variation, clear_variations; enforces max-30 and unique (size, color)
- `design_service.py` — upload with PNG+alpha validation, list+filter, delete with R2 cleanup + composite cache cascade
- `composite_service.py` — `get_or_create_composite`: R2 cache check → download template+design → Pillow composite → R2 upload; rejects `reference_only` designs

#### Tests
- `test_templates_api.py` (~10 tests) — CRUD, auth guards, anchor validation, admin UI list endpoint
- `test_variations_api.py` (~10 tests) — bulk replace, max-30 enforcement, duplicate (size,color) rejection, clear, single update
- `test_designs_api.py` (~10 tests) — RGBA upload, JPEG rejection, RGB-only rejection, size limit, list+filter, delete
- `test_composite_service.py` (~11 tests) — pure function unit tests, cache miss/hit, reference_only rejection, invalidation on update/delete, API endpoint tests
- `test_e2e_template_workflow.py` (4 tests) — full integration: create→6 variations→design→composite miss→hit→invalidate; auth guards; limit enforcement; reference_only rejection

**Total: 125 tests passing** (up from 78 in v0.1.0)

#### Documentation
- `docs/template-system-guide.md` — full user guide: setup env vars, admin UI walkthrough, API reference table, composite anchor ASCII diagram, 5 troubleshooting entries, manual UI checklist
- `docs/system-architecture.md` — added template system Mermaid sequence diagram (template upload → variations → design upload → composite with cache miss/hit/invalidation)
- `docs/codebase-summary.md` — added all new modules with LOC counts
- `docs/development-roadmap.md` — v0.2.0 section with sub-feature B marked complete; A and C listed as next
- `README.md` — added template-system-guide.md link in documentation table, updated test count

### Architecture Decisions
- **Pillow alpha_composite** (not AI/ML): deterministic, free, <5s latency — KISS
- **Manual anchor** (x/y/w/h fractions, 0–1): MVP. Drag-drop anchor UI deferred to future plan
- **R2 cache key** = `composites/{template_id}-{design_id}.png`: simple, invalidated by template/design update via list+delete
- **reference_only source_type**: extension cutouts viewable in design library but excluded from composite (IP risk mitigation)
- **Jinja2 + HTMX admin UI**: zero JS framework, server-rendered, sellers get CRUD without curl

### Known Limitations
- Composite anchor placement is manual — no drag-drop UI yet (future plan)
- Composite is 1 design per template only — multi-layer compositing deferred
- Admin UI requires `X-Admin-Token` in request header — no session cookie auth yet

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
