# Project Changelog

All notable changes to EtsyAuto. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.8.1] — 2026-05-11

Hotfix: `NOTION_SYNC_ENABLED` flag implementation (phase-06 from v0.8 plan was specced but not landed in v0.8.0).

### Fixed

- `app/config.py` — added `notion_sync_enabled: bool = False` setting field (default per v0.8 deprecation plan)
- `app/scheduler.py` — `sync_to_notion` + `pull_approvals` jobs now conditionally registered behind `settings.notion_sync_enabled`; loud startup log when disabled: `Notion sync jobs SKIPPED (NOTION_SYNC_ENABLED=false, v0.8 default)`
- `app/main.py` — `_validate_notion_schema()` (review DB) gated on same flag so users on Notion-deprecated default path no longer trigger startup schema-validate calls. Idea Bank validate runs independently (separate workflow, intentionally unaffected)
- `app/routes/health.py` — `/health` JSON now surfaces `notion_sync_enabled` boolean for admin UI banner

### Added

- `tests/test_scheduler_notion_flag.py` — 6 new tests covering both flag states + non-notion jobs invariant + `/health` exposure
- `scripts/smoke-test-e2e.sh` — new check ensures `/health` exposes `notion_sync_enabled` flag (25 → 26)

### Notes

Pre-existing v0.8.0 release docs (changelog v0.8.0, roadmap, journal) claimed this gating was already in place. It was specced in `plans/260507-1445-idea-to-listing-bridge/phase-06-notion-sync-deprecation.md` but the code never landed before the v0.8.0 tag. This hotfix closes that gap. Test count: 393 → 399.

### Reactivation

Set `NOTION_SYNC_ENABLED=true` in `.env` and restart — both jobs re-register, review-DB validate resumes. No migration required.

---

## [0.8.0] — 2026-05-07

Idea → Listing Bridge + Trending Miner. Closes the gap between extension Idea Bank (v0.3) and Listing Creator (v0.4). Adds Etsy public-API trending miner that hourly snapshots listings for user-managed keywords into a 4-layer idea schema; admin UI for keyword CRUD + idea browsing with velocity sort; 3-step wizard turning any idea into an Etsy draft via existing `listing_creator_service`. Notion sync gated behind env flag.

### Added

#### Backend — schema + miner
- 4 new tables: `keywords` (CRUD + enabled flag), `ideas` (`UNIQUE(source, source_listing_id)`), `idea_signals` (favorers/views timeseries), `idea_to_listing` (composite-PK provenance)
- `EtsyPublicClient` (`x-api-key` only, separate from OAuth) — `search_active_listings`, `get_listing`; honors `ETSY_DRY_RUN` with happy/empty/rate_limit fixtures
- `idea_miner_service.run_for_keyword` + `run_all` (scheduler entrypoint) — fail-closed per listing, 200 ms throttle, idempotent upserts
- `keyword_service` — create, list, toggle enabled, touch_last_run
- `idea_service` — `upsert_idea`, `append_signal`, `latest_signal`, `velocity_per_day`, `link_to_listing`, `mark_drafted`
- New scheduler job `mine_ideas` (hourly cadence, `IDEA_MINER_ENABLED` flag)

#### Backend — admin UI + wizard
- `/admin/keywords` — Jinja+HTMX list, create, toggle, manual fetch
- `/admin/ideas` — list with velocity sort, status/source/keyword filters
- `/admin/ideas/{id}/create-listing` — 3-step wizard (preview → template+design → review+submit)
  - IP-warning banner on Step 1 for `extension_passive` ideas or any idea with a `reference_image_url`
  - Submit reuses `listing_creator_service.create_from_template` end-to-end; on success creates `idea_to_listing` row and flips `idea.status='drafted'`

#### Backend — extension passive log
- `POST /extension/idea` — accepts payload from extension when user visits a public Etsy listing; upserts `source=extension_passive` idea row

#### Tests (118 new, total 392 + 1 new E2E)
- `test_idea_models.py`, `test_keyword_service.py`, `test_idea_service.py`
- `test_etsy_public_client.py`, `test_idea_miner_service.py`, `test_idea_mining_scheduler.py`
- `test_keywords_admin_ui.py`, `test_ideas_admin_ui.py`, `test_idea_wizard.py`
- `test_extension_idea_api.py`
- `test_e2e_idea_to_listing.py` — full happy-path E2E: keyword → miner (dry-run) → wizard → drafted listing

#### Smoke (22 → 25)
- `/admin/keywords` reachable
- `/admin/ideas` reachable
- `/extension/idea` accepts payload (201/200)

#### Docs
- `docs/idea-mining-guide.md` — user-facing how-to
- `docs/journals/v0.8.0-idea-to-listing-bridge.md`
- System architecture: idea-flow diagram + 4-layer schema description

### Changed

- `NOTION_SYNC_ENABLED` env flag (default **false**) — gates both `sync_to_notion` and `pull_approvals` scheduler jobs; loud startup log when disabled. Notion code retained dormant for instant reactivation.
- Extension `manifest.json` 0.4.2 → 0.4.3 — adds passive listing observer that posts to `/extension/idea` on public listing pages
- README test count badge: 274 → 393

### Notes

- `references/scrape` workflow remains; will be deprecated in v0.9 as miner subsumes its role
- Etsy public API quota stays <30% of 10K/day cap with 10 keywords on hourly schedule (200 ms detail throttle)
- IP guidance: reference images are inspiration only — redraw via Imagen before publishing

### Out of Scope (deferred)

- Printful/Printify catalog API (v0.9)
- Idea ↔ POD product matcher (v0.9)
- TeePublic/Society6/Zazzle passive scrape (v1.0)
- Bulk wizard mode (1-by-1 only in v0.8)
- ML/forecasting trend scoring (rule-based velocity only)

---

## [0.7.1] — 2026-05-07

Visual 4-Point Anchor Editor — admin UI for non-dev sellers to drag quad zone corners visually instead of hand-editing JSON.

### Added

#### Backend
- `GET /admin/templates/{id}/anchor` — renders editor page; pre-populates 4 corner points from v1 rect / v2 quad / default
- `POST /admin/templates/{id}/anchor` — JSON `{points: [[x,y]×4]}` → writes v2 schema with single `quad` zone; invalidates composite cache
- `_derive_initial_quad_points` helper — uniform conversion v1 rect → 4 corners (TL, TR, BR, BL)
- `AnchorSavePoints` Pydantic model — validates exactly 4 points, all in [0, 1]

#### Frontend
- `backend/app/templates/templates/anchor-editor.html` — page skeleton with `<img>` base + `<svg>` overlay
- `backend/app/static/anchor-editor.js` — vanilla JS, no libs; pointer events for mouse + touch; clamp [0,1]; cookie-token POST
- `backend/app/static/anchor-editor.css` — handle + polygon styling

#### UI
- "Edit anchor" link in `/admin/templates` list rows

#### Tests (9 new, total 274)
- `test_anchor_editor.py` — page render, v1/v2 pre-pop, save round-trip, 422 (3 points / out-of-bounds), 404 GET/POST, auth-required

#### Smoke (21 → 22)
- Anchor editor page reachable check

### Out of Scope (deferred)
- Multi-zone editing
- rect ↔ quad kind switching
- JSON paste/copy import-export
- Grid snap, alignment guides, undo/redo
- Live composite preview at current zone

---

## [0.7.0] — 2026-05-07

Real-Etsy E2E + Error UX hardening. ETSY_DRY_RUN mode + 5 scenarios for safe end-to-end testing without quota burn. Friendly error mapping for every Etsy 4xx/5xx. Per-request correlation IDs. Pre-flight validation against Etsy hard caps.

### Added

#### Dry-run infrastructure
- `etsy_dry_run` + `etsy_dry_run_scenario` in `app.config.Settings`
- `app/clients/etsy_dry_run_fixtures.py` — 5 scenarios (happy, rate_limit, taxonomy_error, auth_fail, image_too_small)
- `EtsyApiClient` short-circuits all 5 public methods when dry-run is on
- `/health` endpoint exposes `etsy_dry_run` + scenario
- Admin UI banner across all `/admin/*` pages when dry-run is on

#### Correlation IDs
- `app/middleware/correlation_id.py` — middleware sets per-request `X-Request-ID` (uuid4 if not provided)
- Header echoed back on every response
- `_result.html` admin toast renders the ID in collapsed details

#### Error UX
- `app/services/etsy_error_mapper.py` — 6 categories (auth, rate_limit, image_size, taxonomy, etsy_5xx, other, unknown) with user-friendly messages
- Both JSON API (`POST /listings/from-template`) and admin UI (`POST /admin/listings/creator/submit`) now route Etsy errors through the mapper
- Raw Etsy response body still logged (no info loss)

#### Pre-flight checks
- `app/services/listing_pre_check.py` — validates title ≤ 140, tags ≤ 13, combos ≤ 30, composite ≥ 570×570
- `PreCheckFailed` raised with structured `Issue` list before any Etsy call

#### Cleanup
- `scripts/cleanup_placeholder_data.py` — removes templates/designs created with placeholder `cdn.example.com` URLs

#### Tests (18 new, total ≥ 255)
- `test_etsy_dry_run.py` (9) — each scenario, real-HTTP-still-fires-when-off, scenario fallback
- `test_etsy_error_mapper.py` (7) — every error category + non-httpx fallback
- `test_listing_pre_check.py` (7) — title/tags/combos/composite caps, exception payload
- `test_correlation_id_middleware.py` (3) — header echo, inbound preservation, distinct ids
- `test_e2e_dry_run_listing.py` (2) — full pipeline happy + auth_fail through admin UI

#### Smoke (19 → 21)
- `/health` exposes `etsy_dry_run` flag
- `X-Request-ID` round-trip

### Architecture Decisions
- **Inline dry-run, not mock server** — KISS, same client codepath, fewer moving parts
- **Scenario via env, not request param** — config-driven, doesn't pollute API surface
- **Correlation ID via ContextVar** — async-safe, propagates into Etsy log lines
- **Pre-check before Etsy** — fail fast, save quota, friendlier error
- **Both API + admin UI catch error mapper** — symmetric UX

### Known Limitations
- Real OAuth + production runbook deferred to v0.8
- No mock HTTP server (Option A); dry-run is in-process only
- Etsy webhook handling still out of scope

---

## [0.6.0] — 2026-05-07

Template Engine C1 — Quad Zones + Multi-Zone Composites. Anchor schema v2 with zones[] (rect | quad). cv2.warpPerspective for curved/tilted surfaces. Multi-design per template via `zone_designs` map.

### Added

#### Schema
- `composite_anchor_json` schema v2: `{version: 2, zones: [{name, kind, ...}]}`
- v1 → v2 read-time shim (`anchor_schema.parse_anchor`) — no migration; v1 templates continue to work unchanged.

#### Modules
- `app/services/anchor_schema.py` — `parse_anchor` + zone normalization + `to_v2` serializer + `MAX_ZONES = 4` cap
- `app/services/image_composite.composite_quad` — cv2.warpPerspective with INTER_LANCZOS4
- `app/services/image_composite.composite_zones` — orchestrator looping zones, mixing rect (Pillow) + quad (cv2)
- `app/services/composite_service._multi_zone_cache_key` — `composites/{tid}-{hash10}-{Color}-multi.png` for distinct multi-zone designs

#### Routes
- `POST /listings/from-template` body adds optional `zone_designs: dict[str, int] | None`
- `composite_service.get_or_create_composite(..., zone_designs=...)` threads the map through

#### Dependency
- `opencv-python-headless >= 4.10` (transitive: `numpy`)

#### Tests (20 new)
- `test_anchor_schema.py` (8) — v1 shim, v2 parse, malformed JSON, unknown kind skip, max-zones cap, to_v2 upcast
- `test_composite_quad.py` (8) — quad warps to corners, alpha preserved, 4-point validation, output cap, perf gate, layering order, mixed kinds
- `test_e2e_multi_zone.py` (4) — v1 regression, multi-zone cache key, full POST flow with zone_designs, idempotency

### Architecture Decisions
- **Read-time shim, no alembic migration** — v1 templates auto-upcast; on-disk JSON unchanged
- **`opencv-python-headless`** — avoids 80MB Qt deps; works in headless Docker
- **Pillow path preserved for `kind=rect`** — guarantees v1 byte-equivalence
- **Cache key namespace** — single-zone keeps legacy filename; multi-zone uses `-multi.png` suffix → existing R2 cache stays valid
- **Zone layering = array order** — last zone paints on top
- **MAX_ZONES = 4** soft cap; raise if needed

### Known Limitations
- No visual 4-point editor in admin UI (manual JSON only)
- No auto-anchor (deferred to C2)
- No PSD smart-object support (deferred to C3)
- No fabric displacement maps (deferred to C4)

---

## [0.4.0] — 2026-05-06

Etsy Listing Creator (Sub-feature C). End-to-end "create new listing" flow: per-color base images, multi-color composite rendering, full N×M variations matrix, and Etsy draft creation with property-value resolution + sequential image upload.

### Added

#### Database
- `Listing.template_id`, `Listing.design_id` — nullable FKs link a created listing back to the template+design used; idempotency key for `POST /listings/from-template`
- `Template.color_base_images_json` — JSON map `{Color: r2_url}` for per-color blank mockups

#### Alembic Migrations
- `a3d8008fe208_add_color_base_images_json_to_templates.py`
- `7e644acd83eb_add_template_id_design_id_to_listings.py` — adds FKs + composite index `(template_id, design_id)` for idempotency lookup

#### Routes
- `POST /templates/{id}/color-bases/{color}` — upload per-color blank
- `DELETE /templates/{id}/color-bases/{color}` — remove
- `POST /templates/{id}/expand-variations` — auto-build cartesian (size, color) variations from `variation_options_json`
- `POST /composite/preview-all-colors` — parallel render of every template color (ThreadPoolExecutor, max 5 workers)
- `POST /listings/from-template` — create Etsy draft with full inventory matrix + N color images; idempotent on `(template_id, design_id)`

#### Services & Clients
- `services/listing_creator_service.py` — orchestrates: validate → render composites → resolve Etsy property values → create draft → update inventory → upload images → persist Listing
- `services/etsy_taxonomy.py` — Etsy property/value lookup with in-memory cache; hardcoded apparel constants (taxonomy 1209, color=200, size=506)
- `clients/etsy_api_client.py` extended: `create_draft_listing`, `update_listing_inventory`, `upload_listing_image_bytes`, `get_taxonomy_property_values`

#### Extension — Creator Mode
- Content script detects `/your/shops/*/listings/new` + `/create-listing` URLs → mode='creator'
- New `side-panel/creator-mode.js` (~270 LOC) — template+design pickers, variations matrix toggle, multi-color preview grid, "Create Etsy Draft" flow
- `service-worker.js` adds 4 message handlers: `LIST_TEMPLATES`, `LIST_DESIGNS`, `PREVIEW_ALL_COLORS`, `CREATE_LISTING_FROM_TEMPLATE`
- `manifest.json` bumped to v0.4.0

#### Tests (34 new, 197 total — was 163)
- `tests/test_listings_creator_api.py` (9 tests) — auth, validation, happy path, idempotency, image rank order, taxonomy cache, unknown-value 422
- `tests/test_e2e_listing_creator_workflow.py` (3 E2E tests) — full pipeline (create template → color bases → expand → design → preview-all → from-template), idempotent re-call, partial failure on unknown color
- Phase 1 + 2 tests for color-bases API, expand-variations, preview-all-colors

#### Documentation
- `docs/etsy-listing-creator-guide.md` — full walkthrough, API reference, cost breakdown, troubleshooting
- `docs/template-system-guide.md` — new section "Multi-Color Base Images" documenting per-color upload + JSON convention

### Architecture Decisions
- **Per-color base images stored as JSON map on Template** (not new table) — KISS, single migration
- **Etsy draft only, never auto-publish** — seller finalizes from Etsy UI; safer
- **Idempotency via `Listing(template_id, design_id)`** — re-call returns existing `etsy_listing_id` without hitting Etsy
- **Apparel-only taxonomy hardcoded** (1209/200/506); future categories add separate constants
- **Sequential image upload with 200 ms gap** — stays below Etsy 5 req/s soft limit
- **Image rank**: `primary_color` → rank 1, others by `variation_options.colors` order

### Known Limitations
- Apparel taxonomy only (mug/poster/sticker need additional `taxonomy_id` mapping)
- Plain-text description (no rich-text editor in v0.4.0)
- Per-(size, color) pricing not supported — pricing is per-size only
- Composite preview is single-color anchor; design is scaled identically across all color blanks

---

## [0.3.0] — 2026-05-06

Extension Reference Mode (Sub-feature A). Capture inspiration from public Etsy listings: scrape title + images, AI-suggest 3 alternate titles via Gemini, remove background of one image (remove.bg → R2), tag, and save to a Notion Idea Bank database.

### Added

#### Database Models
- `Reference` — scraped public Etsy listing: `listing_id`, `source_url`, `original_title`, `edited_title`, `ai_variants` (JSON), `tags` (JSON), `notes`, `status` ∈ {idea, saved, archived}, optional `cutout_design_id` FK, `notion_page_id`

#### Alembic Migration
- `33596c423a0e_references_table` — creates `references` table with FK to `designs.id`

#### Routes — Reference API
- `POST /references/scrape` — idempotent scrape (returns existing row if `listing_id` already known)
- `GET /references` — list with `status`, `tags` filters
- `GET /references/{id}` — fetch detail
- `PUT /references/{id}` — update `edited_title`, `tags`, `notes`
- `DELETE /references/{id}` — cascades cutout design + R2 cleanup; archives Notion page if saved
- `POST /references/{id}/suggest-title` — Gemini 2.5 Flash returns 3 variants ≤140 chars; replaces existing on re-call
- `POST /references/{id}/cutout` — remove.bg → R2 → creates `Design` with `source_type='reference_only'`; replaces previous cutout on re-call
- `POST /references/{id}/save` — creates/updates Notion Idea Bank page; embeds cutout as image block; sets status=saved

#### Services
- `reference_service.py` — scrape (idempotent), CRUD, suggest-title (Gemini wrapper), cutout (remove.bg + design create), save (Notion data_sources API), schema validation on startup
- Prompt templates: `app/prompts/title-reference-prompt.md` (U1, this release), `title-own-draft-prompt.md` (U2, future sub-feature C scaffold)

#### Extension — Reference Mode
- Content script auto-detects `https://www.etsy.com/listing/*` (broadened from `/your/shops/*` only)
- `etsy-dom-extractor.js` — title + image gallery scrape with og:meta fallback
- `side-panel/reference-mode.js` — Reference Mode UI: thumbnail gallery, AI suggest button, BG remove, tag toggles, notes textarea, Save Reference
- `side-panel/side-panel.js` + CSS + HTML extended with mode switcher (admin vs reference)
- `manifest.json` bumped to v0.3.0

#### Tests (38 new, 163 total)
- `test_references_api.py` (~12 tests) — scrape idempotency, CRUD, auth, filters, cascade delete
- `test_references_ai_cutout_api.py` (~14 tests) — suggest-title 3 variants + replace, cutout creation + replacement, transient 503, source_type filter
- `test_references_notion_save_api.py` (~10 tests) — Notion page create/update idempotency, archive on delete, schema validation, image embed

#### Documentation
- `docs/notion-idea-bank-setup.md` — step-by-step Notion DB creation, required properties + types, integration share, data_source_id retrieval, schema validation
- `docs/reference-workflow-guide.md` — user-facing walkthrough, cost table (~$0.20/reference), troubleshooting, limits

### Architecture Decisions
- **`source_type='reference_only'` cutouts excluded from composite preview dropdowns** — IP risk mitigation; references stay inspiration-only
- **2 prompt templates upfront** — `title-reference-prompt.md` (this release) + `title-own-draft-prompt.md` (sub-feature C scaffold). Single prompts directory, KISS
- **Notion data_sources API** (consistent with v0.1.0 review DB pattern), not the legacy databases endpoint
- **Manual tag toggle** (5 categories: style/color/layout/season/niche) — no auto-classification (YAGNI)
- **Notion mandatory** — `NOTION_IDEA_BANK_DATA_SOURCE_ID` validated on startup, fails loud
- **Cutout cost tracking deferred** — remove.bg dashboard sufficient for v0.3.0; per-day quota guard out of scope

### Known Limitations
- Bulk reference import not supported (anti-bot friendly: 1 click = 1 reference)
- No reverse image search; tags are manual
- One cutout per reference (re-running replaces it)
- Reference cutouts cannot be promoted to upload-type designs (intentional IP boundary)

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
