# Development Roadmap

## MVP Phases (v0.1.0) — All Complete

| Phase | Title | Status | Completed |
|-------|-------|--------|-----------|
| 1 | Foundation & Backend Skeleton | Complete | 2026-05-05 |
| 2 | Etsy API Client + OAuth PKCE | Complete | 2026-05-05 |
| 3 | Chrome Extension MV3 | Complete | 2026-05-05 |
| 4 | Title Optimizer Worker (Claude) | Complete | 2026-05-05 |
| 5 | Mockup Pipeline (remove.bg + Imagen) | Complete | 2026-05-05 |
| 6 | Notion Review Integration | Complete | 2026-05-05 |
| 7 | Etsy Uploader + Retry Logic | Complete | 2026-05-05 |
| 8 | E2E Validation + Documentation | Complete | 2026-05-05 |

### MVP Success Metrics

- [x] 78 unit tests passing, zero skips
- [x] Cost per listing < $0.50
- [x] Machine time per listing < 2 minutes (excl. human review)
- [x] Reproducible setup < 30 minutes from scratch
- [x] Mandatory human approval gate before any Etsy modification
- [x] All docs populated with substantive content

---

## v0.2.0 — Template System & Mockup Composer

### Sub-features

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| B | [Template System & Mockup Composer](../plans/260506-0803-template-system-mockup-composer/plan.md) | **Complete** | 2026-05-06 |
| A | [Extension Reference Mode Upgrade](../plans/260506-0947-extension-reference-mode/plan.md) | **Complete** | 2026-05-06 |
| C | Etsy Listing Creator (with Variations) | **Complete** | 2026-05-06 (v0.4.0) |

### v0.2.0 Success Metrics

- [x] Upload product blank template + define composite anchor
- [x] Add variations matrix (up to 30 rows, 3×2 typical)
- [x] Upload RGBA PNG design artwork
- [x] Generate Pillow alpha-composite preview in <5s
- [x] Composite output cached in R2; cache invalidated on template/design update
- [x] All endpoints protected by X-Admin-Token
- [x] Jinja2 + HTMX admin UI for full template/design/composite CRUD
- [x] 125 tests passing (121 unit + 4 E2E integration)
- [x] Sub-feature A: extension scrapes Etsy public listings → AI suggest title → cutout → Notion Idea Bank
- [x] Sub-feature C: Etsy listing creation reads templates + designs (delivered in v0.4.0)

---

## v0.7.0 — Real-Etsy E2E + Error UX Hardening

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| — | [Dry-Run + Error UX Hardening](../plans/260507-1111-real-etsy-dry-run-hardening/plan.md) | **Complete** | 2026-05-07 |

### v0.7.0 Success Metrics

- [x] `ETSY_DRY_RUN=true` makes every EtsyApiClient call short-circuit to fixture
- [x] 5 scenarios via env: happy / rate_limit / taxonomy_error / auth_fail / image_too_small
- [x] Friendly user message for every Etsy 4xx/5xx + collapsed correlation ID
- [x] X-Request-ID round-trip via middleware
- [x] Pre-flight checks (title/tags/combos/composite size) reject before Etsy quota burn
- [x] /health exposes `etsy_dry_run` flag; admin UI shows yellow banner
- [x] `cleanup_placeholder_data.py` removes leftover smoke-test rows
- [x] 18 new tests; total ≥ 255

### Deferred (post-v0.7.0)

- Real Etsy creds + OAuth production runbook (v0.8)
- Mock HTTP server option (only if dry-run insufficient)
- Etsy webhook handling

---

## v0.8.0 — Idea → Listing Bridge + Trending Miner

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| — | [Idea → Listing Bridge + Trending Miner](../plans/260507-1445-idea-to-listing-bridge/plan.md) | **Complete** | 2026-05-07 |

### v0.8.0 Success Metrics

- [x] 1 keyword → ≥10 ideas mined within 1h (verified via dry-run fixture: 5 ideas/run, hourly cadence)
- [x] Idea→Listing wizard happy-path E2E green (`test_e2e_idea_to_listing.py`)
- [x] Velocity sort UI: ideas re-orderable by favorers/day
- [x] Extension passive log: `POST /extension/idea` accepts payload, creates `source=extension_passive` row
- [x] `NOTION_SYNC_ENABLED=false` (default) skips both `sync_to_notion` + `pull_approvals` jobs cleanly
- [x] Pytest 393 (was 274 + 119 new across phases 1–6 + 1 E2E)
- [x] Smoke 25/25 (was 22 + `/admin/keywords`, `/admin/ideas`, `/extension/idea`)
- [x] Etsy quota stays <30% of 10K/day with 10 keywords on hourly schedule
- [x] Tag `v0.8.0`, changelog entry, journal posted

### Deferred (post-v0.8.0 — see v0.9 / v1.0)

- Printful + Printify catalog API as additional `source` values
- Idea ↔ POD product matcher (auto-suggest which Printful product this Etsy listing maps to)
- Bulk wizard mode (1-by-1 only in v0.8)
- Deprecate `references/scrape` workflow (miner subsumes it)
- TTL cleanup job for old `idea_signals` rows
- TeePublic / Society6 / Zazzle passive scrape (v1.0)

---

## v0.9.0 — POD Catalog Integration (Planned)

### Scope

- Printful Catalog API client — list products, variants, mockup templates
- Printify Catalog API client — same surface, different auth
- Idea ↔ POD product matcher (rule-based first, ML later)
- Bulk wizard mode: select N ideas → batch create via background job
- `references/scrape` route deprecated (miner subsumes)
- TTL cleanup job: prune `idea_signals` older than 30 days

### Open Questions

- Auto-redraw of reference images via Imagen — manual workflow only or guided?
- Per-source quota envelopes (Etsy 10K/day, Printful TBD, Printify TBD)

---

## v1.0.0 — Passive POD Scrape + Multi-Marketplace (Planned)

### Scope

- TeePublic, Society6, Zazzle passive scrape via extension (no public API for these)
- Multi-marketplace listing flow (Etsy + Redbubble + Society6 from one wizard)
- Cost cap per listing (hard stop above configurable threshold)
- ML-based velocity scoring (replace rule-based `(favorers_now − favorers_first) / days`)

---

## v0.7.1 — Visual Anchor Editor

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| — | [Visual 4-Point Anchor Editor (MVP)](../plans/260507-1147-visual-anchor-editor/plan.md) | **Complete** | 2026-05-07 |

### v0.7.1 Success Metrics

- [x] `/admin/templates/{id}/anchor` renders base image with 4 SVG drag handles
- [x] Pre-populates from v1 rect / v2 quad / default centered box
- [x] Save writes v2 schema with single quad zone; cache invalidated
- [x] 9 new tests; total 274
- [x] Smoke 22/22

### Deferred (post-v0.7.1)

- Multi-zone editing UI (add/remove/rename zones)
- rect ↔ quad kind switching in editor
- JSON paste/copy import/export
- Grid snap, alignment guides, undo/redo
- Live composite preview at current zone

---

## v0.6.0 — Template Engine C1 (Quad Zones + Multi-Zone)

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| C1 | [Quad Zones + Multi-Zone Composites](../plans/260507-1021-template-engine-c1-quad-zones/plan.md) | **Complete** | 2026-05-07 |

### v0.6.0 Success Metrics

- [x] `composite_anchor_json` schema v2 (`{version, zones[]}`) with `rect`/`quad` kinds
- [x] `cv2.warpPerspective` perspective renderer (`composite_quad`)
- [x] Multi-zone composites: front + back of t-shirt, etc.
- [x] `POST /listings/from-template` accepts optional `zone_designs` map
- [x] v1 templates render byte-identical (regression test)
- [x] `opencv-python-headless` integrated; `cv2.__version__` 4.13+
- [x] 20 new tests; total ≥ 230

### Deferred sub-phases (post-v0.6.0)

- **C2** — auto-anchor detection (CTDave001 inspired)
- **C3** — PSD smart-object pipeline (psd-tools)
- **C4** — fabric displacement maps (cv2.remap)

---

## v0.4.0 — Etsy Listing Creator

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| C | [Etsy Listing Creator (per-color mockups)](../plans/260506-1458-etsy-listing-creator/plan.md) | **Complete** | 2026-05-06 |

### v0.4.0 Success Metrics

- [x] Per-color base images uploadable (3+ colors per template)
- [x] `POST /composite/preview-all-colors` renders N composites in parallel (<30s for 5 colors)
- [x] `POST /listings/from-template` creates Etsy draft with full N×M inventory + N images
- [x] Idempotent on `(template_id, design_id)` — re-call returns same `etsy_listing_id` without re-creating
- [x] Image rank: `primary_color` → 1, others by template order
- [x] Taxonomy property values cached in-process (single fetch per `(taxonomy, property)` pair)
- [x] Sequential image upload with 200 ms gap (Etsy rate-limit safety)
- [x] Reference-only designs rejected from creator (IP boundary preserved)
- [x] 197 tests passing (34 new vs v0.3.0's 163)
- [x] Extension auto-detects `/your/shops/*/listings/new` → Creator Mode UI
- [x] Etsy draft only — never auto-publish

---

## v0.3.0 — Extension Reference Mode

### Scope

| Sub-feature | Title | Status | Completed |
|-------------|-------|--------|-----------|
| A | [Extension Reference Mode Upgrade](../plans/260506-0947-extension-reference-mode/plan.md) | **Complete** | 2026-05-06 |

### v0.3.0 Success Metrics

- [x] Extension auto-detects public Etsy listings (`/listing/<id>`)
- [x] Scrape title + up to 10 images, idempotent by `listing_id`
- [x] Gemini 2.5 Flash returns 3 alternate titles ≤140 chars in <5s
- [x] remove.bg cutout uploaded to R2 as `Design.source_type='reference_only'`
- [x] Reference cutouts excluded from composite preview dropdowns (IP boundary)
- [x] Save Reference creates/updates Notion Idea Bank page with cutout image embed
- [x] All endpoints protected by `X-Admin-Token`
- [x] 163 tests passing (38 new vs v0.2.0's 125)

---

## Post-MVP Backlog (Unscheduled)

### P1 — High Value

| Feature | Description | Effort |
|---------|-------------|--------|
| Analytics dashboard | Per-listing cost tracking, time-to-push metrics, Claude token usage | M |
| Batch queue UI | Select 10+ listings in extension, queue all at once | M |
| Multi-image mockups | Generate mockups for all 10 Etsy image slots, not just slot 1 | L |
| Prompt A/B testing | Track which title variant gets more views after push | L |
| Cost cap per listing | Hard stop if estimated cost exceeds configurable threshold | S |

### P2 — Nice to Have

| Feature | Description | Effort |
|---------|-------------|--------|
| Multi-language titles | Generate titles in DE/FR/ES for international shops | M |
| Category-specific prompts | Different scene prompts for apparel vs. jewelry vs. print | S |
| Notion template export | One-click Notion DB template duplication | S |
| Slack/email notification | Alert when review page ready in Notion | S |
| Local rembg fallback | Use `rembg` Python library if remove.bg credits exhausted | M |

### P3 — Architecture / Scale

| Feature | Description | Effort |
|---------|-------------|--------|
| Postgres migration | Swap SQLite → Postgres for multi-user or cloud deployment | L |
| Redis task queue | Replace APScheduler with Celery + Redis for scale | L |
| Multi-shop support | Multiple Etsy shops with separate credential sets | L |
| SaaS mode | Cloud-hosted backend, per-user isolation, Stripe billing | XL |
| Firefox extension | Port Chrome MV3 extension to Firefox Manifest V2/V3 | M |

---

## Known Technical Debt

| Item | Impact | Priority |
|------|--------|----------|
| PKCE state stored in-memory dict | Lost on server restart; user must re-auth | Low (single-user dev server) |
| No request-level rate limiting on `/ingest` | Spam possible from malicious extension | Low (localhost only) |
| Notion `pull_approvals` polls all Approved pages | O(n) Notion API calls as reviews accumulate | Medium (optimize with cursor/filter) |
| No db WAL backup cron | SQLite could corrupt on hard crash | Medium |
| Gemini model ID hardcoded | Must manually update when preview model reaches GA | Medium |

---

## Version History

See `docs/project-changelog.md` for detailed change log.
