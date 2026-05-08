# Codebase Summary

Module-by-module index with line counts. Updated: 2026-05-07 (v0.8.0).

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
| `routes/templates_api.py` | 187 | `GET/POST /templates`, `GET/PUT/DELETE /templates/{id}` — template CRUD, X-Admin-Token protected |
| `routes/variations_api.py` | 145 | `GET/POST /templates/{id}/variations`, `PUT/DELETE` variations — bulk replace + single update |
| `routes/designs_api.py` | 104 | `GET/POST /designs`, `GET/DELETE /designs/{id}` — design upload/list/delete, RGBA PNG validation |
| `routes/composite_api.py` | 56 | `POST /composite/preview` — trigger Pillow alpha-composite, return cached R2 URL |
| `routes/templates_admin.py` | 590 | Jinja2 + HTMX admin UI — `/admin/templates` CRUD, designs, composite preview, **listing creator (v0.5.0)** |
| `routes/references_api.py` | 329 | `POST /references/scrape`, `GET/PUT/DELETE /references/{id}`, `/{id}/suggest-title`, `/{id}/cutout`, `/{id}/save` — reference workflow, X-Admin-Token protected |
| `routes/listings_creator_api.py` | 83 | `POST /listings/from-template` — idempotent Etsy draft creator (sub-feature C, v0.4.0) |
| `routes/keywords_admin.py` | 237 | `/admin/keywords` Jinja+HTMX CRUD — list, create, toggle enabled, manual fetch (v0.8.0) |
| `routes/ideas_admin.py` | 202 | `/admin/ideas` browse — velocity sort, filters by status/source/keyword (v0.8.0) |
| `routes/idea_wizard.py` | 445 | 3-step Idea→Listing wizard — `/admin/ideas/{id}/create-listing` step1/step2/step3/submit (v0.8.0) |
| `routes/extension_idea_api.py` | 87 | `POST /extension/idea` — passive log endpoint, upserts `source=extension_passive` ideas (v0.8.0) |

### Models (`app/models/`)

| File | LOC | Purpose |
|------|-----|---------|
| `models/listing.py` | 37 | `Listing` table — core state machine, etsy_listing_id, status, notion_page_id, template_id+design_id (v0.4.0 idempotency) |
| `models/title_variant.py` | 20 | `TitleVariant` — 3 Claude-generated variants per listing |
| `models/mockup_variant.py` | 22 | `MockupVariant` — 3 Imagen-generated variants with R2 URLs |
| `models/job.py` | 21 | `Job` — async task execution tracking |
| `models/api_credential.py` | 17 | `ApiCredential` — Etsy OAuth tokens (access + refresh) |
| `models/template.py` | 32 | `Template` — product blank image, composite anchor JSON, variation_options JSON, R2 URL |
| `models/template_variation.py` | 24 | `TemplateVariation` — size/color/price_cents/sku row; max 30 per template (Etsy limit) |
| `models/design.py` | 25 | `Design` — uploaded artwork PNG; source_type ∈ {upload, ai_generated, reference_only} |
| `models/reference.py` | 43 | `Reference` — scraped public Etsy listing: listing_id, source_url, original/edited title, ai_variants, tags, notes, status, optional cutout_design_id FK, notion_page_id |
| `models/keyword.py` | 24 | `Keyword` — user-managed search term + enabled flag + last_run_at; FK target for ideas (v0.8.0) |
| `models/idea.py` | 56 | `Idea` — UNIQUE on `(source, source_listing_id)`; status ∈ {new, saved, drafted, dismissed}; reference_image_url, tags_json, price_cents (v0.8.0) |
| `models/idea_signal.py` | 34 | `IdeaSignal` — timeseries of `(idea_id, captured_at, num_favorers, views_all_time)` for velocity sort (v0.8.0) |
| `models/idea_to_listing.py` | 29 | `IdeaToListing` — composite-PK provenance link `(idea_id, listing_id)` (v0.8.0) |

| File | LOC | Purpose |
|------|-----|---------|
| `workers/title_optimizer.py` | 64 | Picks `status=new` listings → calls Etsy API to enrich → calls Claude → stores 3 variants → sets `mockup-pending` |
| `workers/mockup_pipeline.py` | 109 | Picks `status=mockup-pending` → remove.bg → Gemini Imagen ×3 → R2 upload → sets `review` |
| `workers/notion_sync.py` | 169 | `sync_to_notion`: creates Notion review page with title/mockup blocks. `pull_approvals`: polls Notion for Approved status → updates SQLite |
| `workers/etsy_uploader.py` | 174 | Picks `status=approved` → PATCH Etsy listing title → POST listing image → sets `pushed`; exponential backoff on failure |

### Services (`app/services/`)

| File | LOC | Purpose |
|------|-----|---------|
| `services/listing_service.py` | 207 | DB query helpers — fetch listings by status, update status, get variants |
| `services/image_service.py` | 127 | PIL utilities — resize, composite, format conversion for mockup pipeline |
| `services/review_service.py` | 107 | Notion review logic — build page blocks, parse approval response |
| `services/retry_policy.py` | 25 | Exponential backoff decorator + `should_retry(attempts)` helper |
| `services/image_composite.py` | 183 | `composite_with_anchor(base, design, anchor)` — Pillow RGBA alpha-paste with bounds clamping |
| `services/template_service.py` | 154 | Template CRUD helpers — create/update/delete with R2 cleanup + composite cache invalidation |
| `services/variation_service.py` | 102 | Bulk replace, list, update, clear variations; enforces max-30 and unique (size, color) |
| `services/design_service.py` | 156 | Design upload/list/delete with PNG+alpha validation, R2 upload, composite cache cascade |
| `services/composite_service.py` | 134 | `get_or_create_composite` — cache-check → Pillow composite → R2 upload; rejects reference_only |
| `services/reference_service.py` | 364 | Reference scrape (idempotent), CRUD, Gemini suggest-title (3 variants), remove.bg cutout → `Design.source_type='reference_only'`, Notion Idea Bank save (data_sources API + image embed), schema validation on startup |
| `services/etsy_taxonomy.py` | 117 | Etsy taxonomy + property value lookup with in-memory cache; apparel constants (taxonomy 1209, color=200, size=506) |
| `services/listing_creator_service.py` | 295 | Orchestrator: composite all colors → resolve property values → Etsy create draft → update inventory → upload images sequentially → persist Listing |
| `services/anchor_schema.py` | 130 | Anchor schema v2 parser — `parse_anchor` (v1 shim → zones[] envelope), `to_v2` serializer, `MAX_ZONES=4` (v0.6.0) |
| `services/etsy_error_mapper.py` | 80 | Map httpx.HTTPStatusError → user-friendly toast (auth/rate/image/taxonomy/5xx) (v0.7.0) |
| `services/listing_pre_check.py` | 90 | Etsy hard-cap validation (title/tags/combos/composite) before Etsy call (v0.7.0) |
| `services/keyword_service.py` | 121 | Keyword CRUD — create, list, toggle enabled, touch_last_run (v0.8.0) |
| `services/idea_service.py` | 354 | Idea CRUD — `upsert_idea`, `append_signal`, `latest_signal`, `velocity_per_day`, `link_to_listing`, `mark_drafted` (v0.8.0) |
| `services/idea_miner_service.py` | 223 | Etsy public API → idea pipeline — `run_for_keyword`, `run_all` (scheduler), fail-closed per listing (v0.8.0) |
| `clients/etsy_dry_run_fixtures.py` | 130 | Canned Etsy v3 responses for ETSY_DRY_RUN — 5 scenarios (v0.7.0) |
| `middleware/correlation_id.py` | 45 | Per-request X-Request-ID via ContextVar (v0.7.0) |
| `app/static/anchor-editor.js` | ~130 | Vanilla JS controller for the visual editor (v0.7.1) |
| `app/static/anchor-editor.css` | ~32 | SVG handle + polygon styles |
| `app/templates/templates/anchor-editor.html` | ~30 | Editor page skeleton |

### Clients (`app/clients/`)

| File | LOC | Purpose |
|------|-----|---------|
| `clients/claude_client.py` | 124 | Anthropic SDK wrapper — `generate_title_variants(title, desc, tags)` → `list[TitleVariantData]` |
| `clients/etsy_api_client.py` | 321 | Etsy Open API v3 — `get_listing/update_listing/upload_listing_image` (v0.1.0); `create_draft_listing/update_listing_inventory/upload_listing_image_bytes/get_taxonomy_property_values` (v0.4.0) |
| `clients/etsy_oauth.py` | 145 | PKCE helpers — `generate_pkce_pair()`, `build_auth_url()`, `exchange_code()`, `get_valid_token()` |
| `clients/gemini_imagen_client.py` | 82 | Google GenAI SDK wrapper — `generate_mockup_image(prompt)` → base64 PNG |
| `clients/notion_client.py` | 243 | Notion API wrapper — `create_review_page()`, `query_approved_listings()`, `validate_database_schema()` |
| `clients/r2_storage_client.py` | 64 | boto3 S3-compatible client — `upload_image(key, data)` → public URL |
| `clients/removebg_client.py` | 57 | remove.bg REST client — `remove_background(image_url)` → PNG bytes |
| `clients/etsy_public_client.py` | 197 | Public x-api-key client — `search_active_listings`, `get_listing`; honors ETSY_DRY_RUN (v0.8.0) |
| `clients/etsy_public_dry_run_fixtures.py` | 224 | Canned public-API responses (happy/empty/rate_limit) for offline tests (v0.8.0) |

### Prompts (`app/prompts/`)

| File | LOC | Purpose |
|------|-----|---------|
| *(claude prompt templates)* | — | Claude system/user prompt strings for v0.1.0 title generation |
| `title-reference-prompt.md` | 16 | Gemini prompt for AI Suggest Title from a scraped Etsy reference (sub-feature A) |
| `title-own-draft-prompt.md` | 16 | Gemini prompt scaffold for user's own draft titles (reserved for sub-feature C) |

### Migrations (`alembic/versions/`)

| File | Purpose |
|------|---------|
| `0001_initial.py` | Base schema — listings, title_variants, mockup_variants, jobs, api_credentials |
| `aa88c2baf005_*.py` | Add `push_attempts`, `last_push_error` columns to listings |
| `e7d4a402ca7b_*.py` | Add `notion_page_id`, `final_image_url` columns to listings |
| `240d6e765e57_*.py` | Add template system — `templates`, `template_variations`, `designs` tables |
| `33596c423a0e_references_table.py` | Add `references` table with FK to `designs.id` (sub-feature A) |
| `a3d8008fe208_*.py` | Add `color_base_images_json` to templates (sub-feature C) |
| `7e644acd83eb_*.py` | Add `template_id`, `design_id` to listings + `(template_id, design_id)` index (sub-feature C idempotency) |

### Tests (`tests/`)

| File | Tests | Coverage Target |
|------|-------|----------------|
| `test_ingest.py` | ~12 | POST /ingest happy path, idempotency, terminal status reset |
| `test_title_optimizer.py` | ~14 | Title generation, Claude mock, status transitions |
| `test_mockup_pipeline.py` | ~14 | remove.bg mock, Imagen mock, R2 upload mock |
| `test_notion_sync.py` | ~14 | Page creation, approval polling, schema validation |
| `test_etsy_api_client.py` | ~12 | Listing fetch, title update, image upload |
| `test_etsy_uploader.py` | ~12 | Uploader job, retry logic, push_attempts increment |
| `test_templates_api.py` | ~10 | Template CRUD, auth guards, anchor validation, admin UI list |
| `test_variations_api.py` | ~10 | Bulk replace, max-30 limit, duplicate rejection, clear, price update |
| `test_designs_api.py` | ~10 | Upload RGBA PNG, reject JPEG/RGB/oversized, list+filter, delete |
| `test_composite_service.py` | ~11 | Cache miss/hit, reference_only rejection, cache invalidation on update/delete |
| `test_e2e_template_workflow.py` | 4 | Full workflow: create→variations→design→composite with cache miss/hit/invalidation |
| `test_references_api.py` | ~12 | Scrape idempotency, CRUD, auth guards, tag/status filter, cascade delete |
| `test_references_ai_cutout_api.py` | ~14 | Suggest-title 3 variants + replace, cutout creation + replacement, transient 503, source_type filter exclusion |
| `test_references_notion_save_api.py` | ~10 | Notion page create/update idempotency, archive on delete, schema validation, image embed |
| `test_templates_color_bases_api.py` | ~8 | Per-color base upload/delete, validation against `variation_options.colors`, replacement |
| `test_listings_creator_api.py` | 9 | Auth, validation, happy path, idempotency, image rank order, taxonomy cache, unknown-value 422 |
| `test_e2e_listing_creator_workflow.py` | 3 | Full pipeline (template → color bases → expand → design → preview-all → from-template), idempotent re-call, partial-failure 422 |
| `test_listings_creator_admin_ui.py` | 18 | Admin UI routes — auth, page render, template-info partial, preview/submit form parse, idempotent badge, error mapping, login/logout, name-collision regression (v0.5.0+) |
| `test_anchor_schema.py` | 8 | v1 shim, v2 parse, malformed JSON, unknown-kind skip, max-zones cap, to_v2 upcast (v0.6.0) |
| `test_composite_quad.py` | 8 | cv2.warpPerspective correctness, alpha preserve, 4-point validation, output cap, perf gate, layering order, mixed rect+quad (v0.6.0) |
| `test_e2e_multi_zone.py` | 4 | v1 regression via zone pipeline, multi-zone cache key, full POST flow with zone_designs, idempotency (v0.6.0) |
| `test_etsy_dry_run.py` | 9 | Each dry-run scenario, real-HTTP-still-fires-when-off, scenario fallback (v0.7.0) |
| `test_etsy_error_mapper.py` | 7 | All error categories + non-httpx fallback (v0.7.0) |
| `test_listing_pre_check.py` | 7 | Title/tags/combos/composite caps, exception payload (v0.7.0) |
| `test_correlation_id_middleware.py` | 3 | X-Request-ID echo, inbound preservation, distinct IDs (v0.7.0) |
| `test_e2e_dry_run_listing.py` | 2 | Full pipeline happy + auth_fail through admin UI under dry-run (v0.7.0) |
| `test_anchor_editor.py` | 9 | Page render, v1/v2 pre-pop, save round-trip, 422 (3 points / out-of-bounds), 404 GET/POST, auth-required (v0.7.1) |

**Total: 274 tests passing**

---

## Extension (`extension/`)

| File | LOC | Purpose |
|------|-----|---------|
| `manifest.json` | ~50 | MV3 manifest — host_permissions covers `/your/shops/*` + `/listing/*`, side_panel, content_scripts; v0.4.0 |
| `side-panel/creator-mode.js` | 272 | Creator UI: template/design pickers, variations matrix toggle, multi-color preview grid, Create Etsy Draft button (sub-feature C) |
| `background/service-worker.js` | ~210 | Handles messages from content script, routes admin vs reference mode, calls backend API |
| `side-panel/side-panel.html` | ~130 | Side panel UI shell with mode switcher (admin / reference) |
| `side-panel/side-panel.js` | 224 | Side panel logic — admin listing flow, mode detection, dispatches to reference-mode.js |
| `side-panel/reference-mode.js` | 332 | Reference Mode UI — image gallery, AI suggest, BG remove, tag toggles, notes, save flow |
| `side-panel/side-panel.css` | ~250 | Side panel styles incl. reference mode gallery + tag chips |
| `content-scripts/listing-detector.js` | ~120 | Detects admin shop pages and public `/listing/*`, dispatches to scrape vs ingest |
| `content-scripts/listing-detector.css` | ~20 | Injected styles for detection indicators |
| `shared/etsy-dom-extractor.js` | ~150 | Title + image gallery scrape with og:meta fallback (admin + public) |
| `shared/backend-client.js` | ~250 | Backend HTTP wrapper — admin endpoints + reference endpoints |

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
| `template-system-guide.md` | Template + variations + composite preview UI/API; multi-color base images section (v0.4.0) |
| `reference-workflow-guide.md` | Reference Mode walkthrough (sub-feature A, v0.3.0) |
| `etsy-listing-creator-guide.md` | End-to-end Creator Mode walkthrough — per-color mockups, variations matrix, Etsy draft creation (sub-feature C, v0.4.0) + Admin UI alternative (v0.5.0) |

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
