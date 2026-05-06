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
