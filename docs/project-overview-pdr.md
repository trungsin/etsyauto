# EtsyAuto — Project Design Record (PDR)

## 1. Problem Statement

Etsy sellers managing 50–500+ active listings spend 2–4 hours per listing crafting SEO-optimized titles, generating lifestyle mockup images, and manually updating listings. This is repetitive, high-effort, and produces inconsistent results. Human judgment is still required for final approval, but the mechanical work can be automated.

## 2. Solution

EtsyAuto is a local-first listing optimizer that:
1. Captures a listing from the Etsy seller dashboard via a Chrome MV3 extension
2. Generates 3 AI-powered title variants using Anthropic Claude Sonnet 4.6
3. Generates 3 lifestyle mockup image variants using remove.bg + Gemini Imagen
4. Presents variants in a Notion review page for human approval
5. Pushes the approved title and mockup image back to Etsy via the official API

The system is entirely local (SQLite, localhost backend) — no SaaS subscription, no data leaves the machine except to the configured AI APIs.

## 3. Goals

| Goal | Metric |
|------|--------|
| Reduce manual time per listing | < 2 minutes machine time (excluding review) |
| Cost per listing | < $0.50 (all AI API calls combined) |
| Human control | Mandatory Notion approval gate before any Etsy update |
| Reproducibility | Fresh machine → running in < 30 minutes |
| Test coverage | ≥ 78 passing unit tests, no mocks in business logic |

## 4. Non-Goals (MVP)

- Multi-shop support
- Analytics dashboard / cost tracking UI
- Bulk batch scheduling UI
- Multi-language title generation
- Video mockup generation
- SaaS / cloud deployment

## 5. Architecture Decisions

### 5.1 Local SQLite over cloud DB
- Zero infra cost
- No network latency for scheduler polling
- Single-user MVP — no concurrent write contention
- Migration path: swap `DATABASE_URL` to Postgres when scaling

### 5.2 APScheduler over Celery / task queue
- No Redis/RabbitMQ dependency
- Background thread scheduler sufficient for 1-user polling model
- 5 jobs: `title_optimizer` (30s), `sync_to_notion` (30s), `pull_approvals` (60s), `mockup_pipeline` (60s), `etsy_uploader` (600s)

### 5.3 Notion as review UI
- Zero UI development cost
- Sellers already use Notion for business ops
- Rich media (images, callouts) native to Notion
- Alternative considered: custom React UI — deferred to post-MVP

### 5.4 Cloudflare R2 for image hosting
- Notion pages require public HTTPS image URLs for embeds
- R2 free tier: 10 GB storage + 10M Class A ops/month
- Presigned URLs or public bucket both viable; public bucket used for simplicity

### 5.5 Chrome MV3 extension (not scraper)
- Etsy ToS prohibits automated scraping
- Extension injects only on `www.etsy.com` listing pages
- Sends listing_id + visible title/images to localhost — seller initiates action

## 6. Status State Machine

```
new
 │
 ├─► title-processing ──► (title variants created) ──► mockup-pending
 │         │ (fail)                                           │
 │         └─► failed                               mockup-processing
 │                                                            │ (fail)
 │                                                            └─► failed
 │                                                            │ (done)
 │                                                     review (Notion page synced)
 │                                                            │
 │                                                   (human sets Approved in Notion)
 │                                                            │
 │                                                       approved
 │                                                            │
 │                                                       pushing
 │                                                            │ (fail)
 │                                                            └─► failed (retriable)
 │                                                            │ (done)
 │                                                        pushed ✓
```

## 7. Cost Breakdown (per listing, typical)

| Service | Call | Est. Cost |
|---------|------|-----------|
| Anthropic Claude Sonnet 4.6 | ~1500 tokens in + 600 tokens out × 3 variants | ~$0.02 |
| remove.bg | 1 background removal | $0.09–$0.20 (volume tier) |
| Gemini Imagen (gemini-2.5-flash-preview-05-20) | 3 image generations | ~$0.01–$0.05 |
| Cloudflare R2 | 3 writes + reads (< 1 MB total) | < $0.001 |
| **Total** | | **< $0.30 typical, < $0.50 cap** |

## 8. Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI 0.136+ / Python 3.12 |
| Scheduler | APScheduler 3.11 (BackgroundScheduler) |
| Database | SQLite via SQLAlchemy 2.0 ORM + Alembic migrations |
| Title AI | Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| Background removal | remove.bg REST API |
| Image generation | Google Gemini (`gemini-2.5-flash-preview-05-20`) |
| Image hosting | Cloudflare R2 (S3-compatible) |
| Review UI | Notion API v1 (database + page blocks) |
| Etsy integration | Etsy Open API v3 (PKCE OAuth 2.0) |
| Browser extension | Chrome MV3 — service worker + side panel |
| Package manager | uv (fast Python package manager) |

## 9. Project Timeline

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation & backend skeleton | Complete |
| 2 | Etsy API client + OAuth | Complete |
| 3 | Chrome extension MV3 | Complete |
| 4 | Title optimizer worker (Claude) | Complete |
| 5 | Mockup pipeline (remove.bg + Imagen) | Complete |
| 6 | Notion review integration | Complete |
| 7 | Etsy uploader + retry logic | Complete |
| 8 | E2E validation + documentation | Complete |

## 10. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Etsy API ToS change | Low | Extension uses official API only; monitor changelog |
| Gemini Imagen model rename (preview → GA) | Medium | Switch `gemini-2.5-flash-preview-05-20` → `gemini-2.5-flash-image` when GA |
| remove.bg price increase | Low | Fallback: PIL-based simple background removal or rembg local model |
| Notion API breaking change | Low | Pin `notion-client==3.0.0`; monitor changelogs |
| SQLite WAL corruption on crash | Low | Daily backup cron; WAL mode enabled by default in SQLAlchemy |
