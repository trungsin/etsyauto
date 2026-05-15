# System Architecture

## Component Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Seller's Machine (localhost)                                        │
│                                                                      │
│  ┌──────────────────┐        ┌──────────────────────────────────┐   │
│  │  Chrome Browser  │        │  FastAPI Backend  :8787           │   │
│  │  ┌────────────┐  │  HTTP  │  ┌────────────┐  ┌────────────┐  │   │
│  │  │  Side Panel│◄─┼────────┼─►│  /ingest   │  │  /health   │  │   │
│  │  │  (UI)      │  │        │  │  /auth/etsy│  │  /admin    │  │   │
│  │  └────────────┘  │        │  └────────────┘  └────────────┘  │   │
│  │  ┌────────────┐  │        │                                   │   │
│  │  │  Content   │  │        │  ┌──────────────────────────────┐ │   │
│  │  │  Script    │  │        │  │  APScheduler (BackgroundSched)│ │   │
│  │  │  (detect   │  │        │  │  • title_optimizer    (30s)  │ │   │
│  │  │   listing) │  │        │  │  • mockup_pipeline    (60s)  │ │   │
│  │  └────────────┘  │        │  │  • sync_to_notion     (30s)  │ │   │
│  │  ┌────────────┐  │        │  │  • pull_approvals     (60s)  │ │   │
│  │  │  Service   │  │        │  │  • etsy_uploader      (600s) │ │   │
│  │  │  Worker    │  │        │  └──────────────────────────────┘ │   │
│  │  └────────────┘  │        │                                   │   │
│  └──────────────────┘        │  ┌──────────────────────────────┐ │   │
│                               │  │  SQLite (etsyauto.db)        │ │   │
│                               │  │  • listings                  │ │   │
│                               │  │  • title_variants            │ │   │
│                               │  │  • mockup_variants           │ │   │
│                               │  │  • jobs                      │ │   │
│                               │  │  • api_credentials           │ │   │
│                               │  └──────────────────────────────┘ │   │
│                               └──────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
        │                │              │             │
        ▼                ▼              ▼             ▼
   Etsy API v3     Anthropic API   Google Gemini   remove.bg
   (OAuth PKCE)    (Claude Sonnet) (Imagen)        (REST API)
        │
        ▼
   Cloudflare R2 ◄──────────────────────────────────────────
   (Image store)                                             │
        │                                                    │
        ▼                                                    │
   Notion API ─────────────────────────────────────► Seller Review
   (DB + page)                                       (Notion UI)
```

## Data Flow Sequence

```mermaid
sequenceDiagram
    participant Ext as Chrome Extension
    participant API as FastAPI :8787
    participant DB as SQLite
    participant TW as title_optimizer worker
    participant MW as mockup_pipeline worker
    participant NT as sync_to_notion worker
    participant PA as pull_approvals worker
    participant EU as etsy_uploader worker
    participant Claude as Anthropic Claude
    participant RBG as remove.bg
    participant Img as Gemini Imagen
    participant R2 as Cloudflare R2
    participant Notion as Notion API
    participant Etsy as Etsy API v3

    Ext->>API: POST /ingest {listing_id, title, images}
    API->>DB: INSERT listing (status=new)
    API-->>Ext: {job_id, status: "new"}

    Note over TW: polls every 30s
    TW->>DB: SELECT listings WHERE status='new'
    TW->>DB: UPDATE status='title-processing'
    TW->>Etsy: GET /listings/{id} (enrich title/tags/desc)
    TW->>Claude: Generate 3 title variants
    Claude-->>TW: [{title, rationale, tags}×3]
    TW->>DB: INSERT title_variants (×3)
    TW->>DB: UPDATE listing status='mockup-pending'

    Note over MW: polls every 60s
    MW->>DB: SELECT listings WHERE status='mockup-pending'
    MW->>DB: UPDATE status='mockup-processing'
    MW->>RBG: Remove background from original image
    RBG-->>MW: PNG with transparency
    loop 3 times
        MW->>Img: Generate lifestyle scene with product
        Img-->>MW: Base64 PNG
        MW->>R2: PUT mockup_variant_{n}.png
        R2-->>MW: public URL
        MW->>DB: INSERT mockup_variant (url)
    end
    MW->>DB: UPDATE listing status='review'

    Note over NT: polls every 30s
    NT->>DB: SELECT listings WHERE status='review' AND notion_page_id IS NULL
    NT->>Notion: Create page in DB with title variants + mockup images
    Notion-->>NT: page_id
    NT->>DB: UPDATE listing notion_page_id=page_id

    Note over PA: polls every 60s — Seller approves in Notion UI
    PA->>Notion: Query DB for status='Approved'
    Notion-->>PA: [{page_id, selected_title, selected_mockup}]
    PA->>DB: UPDATE listing status='approved', set selected=True on variants

    Note over EU: polls every 600s
    EU->>DB: SELECT listings WHERE status='approved'
    EU->>DB: UPDATE status='pushing'
    EU->>Etsy: PATCH /listings/{id} (title update)
    EU->>Etsy: POST /listings/{id}/images (upload mockup)
    Etsy-->>EU: 200 OK
    EU->>DB: UPDATE status='pushed', pushed_at=now()
```

## Template System Flow (Sub-feature B)

```mermaid
sequenceDiagram
    participant Admin as Admin (curl / Browser UI)
    participant API as FastAPI :8787
    participant DB as SQLite
    participant Pillow as Pillow Service
    participant R2 as Cloudflare R2

    Note over Admin,R2: 1. Upload product blank template

    Admin->>API: POST /templates (multipart: name, category, anchor, base_image)
    API->>API: Validate anchor values in [0,1]
    API->>R2: PUT templates/{uuid}.png
    R2-->>API: public URL
    API->>DB: INSERT templates (name, category, base_image_url, composite_anchor_json)
    API-->>Admin: 201 {id, name, base_image_url, composite_anchor}

    Note over Admin,R2: 2. Add variations matrix (3 sizes × 2 colors = 6 rows)

    Admin->>API: POST /templates/{id}/variations {variations: [{size,color,price_cents}×6]}
    API->>API: Validate count ≤ 30, unique (size,color) pairs
    API->>DB: DELETE old variations WHERE template_id={id}
    API->>DB: INSERT new variations (×6)
    API-->>Admin: 200 {template_id, variations: [...×6]}

    Note over Admin,R2: 3. Upload design artwork (RGBA PNG)

    Admin->>API: POST /designs (multipart: name, source_type, file)
    API->>API: Validate PNG + alpha channel, size ≤ 10MB
    API->>R2: PUT designs/{uuid}.png
    R2-->>API: public URL
    API->>DB: INSERT designs (name, source_type, file_url, width, height)
    API-->>Admin: 201 {id, name, source_type, file_url, width, height}

    Note over Admin,R2: 4. Generate composite preview (Pillow alpha-paste)

    Admin->>API: POST /composite/preview {template_id, design_id}
    API->>DB: SELECT template WHERE id={template_id}
    API->>DB: SELECT design WHERE id={design_id}
    API->>API: Reject if design.source_type == reference_only
    API->>R2: HEAD composites/{template_id}-{design_id}.png (cache check)
    alt Cache HIT
        R2-->>API: Object exists → return public URL
        API-->>Admin: 200 {composite_url, cached: true}
    else Cache MISS
        API->>R2: GET base_image_url (download template PNG)
        R2-->>API: template bytes
        API->>R2: GET design.file_url (download design PNG)
        R2-->>API: design bytes
        API->>Pillow: composite_with_anchor(base, design, anchor)
        Pillow->>Pillow: Resize design to anchor region (LANCZOS)
        Pillow->>Pillow: Image.alpha_composite(base, design_layer)
        Pillow-->>API: composite PNG bytes
        API->>R2: PUT composites/{template_id}-{design_id}.png
        R2-->>API: public URL
        API-->>Admin: 200 {composite_url, cached: false}
    end

    Note over Admin,R2: 5. Cache invalidation on template update

    Admin->>API: PUT /templates/{id} {default_price_cents: 3000}
    API->>DB: UPDATE templates SET default_price_cents=3000
    API->>R2: LIST composites/{id}-*.png
    R2-->>API: [{key: "composites/{id}-{design_id}.png"}]
    loop for each cached composite key
        API->>R2: DELETE composites/{id}-{design_id}.png
    end
    API-->>Admin: 200 {id, default_price_cents: 3000, ...}
```

## Idea → Listing Flow (v0.8.0)

```
                   ┌─────────────────────────┐
                   │  Etsy public API v3      │
                   │  /listings/active        │
                   └──────────┬──────────────┘
                              │ keyword search (x-api-key)
                              ▼
   ┌─────────────────┐   ┌──────────────────────────────┐
   │ APScheduler     │──▶│ idea_miner_service           │
   │ (hourly)        │   │ - per-keyword search          │
   └─────────────────┘   │ - get_listing detail          │
                         │ - upsert idea + signal        │
                         │ - fail-closed per listing     │
                         └──────────┬───────────────────┘
                                    │
                                    ▼
   ┌──────────────────────────────────────────────────┐
   │ SQLite (4-layer schema)                           │
   │ ┌──────────────────────────────────────────────┐ │
   │ │ keywords (id, term, enabled, last_run_at)    │ │
   │ │ ideas (UNIQUE source+source_listing_id,       │ │
   │ │   status: new|saved|drafted|dismissed)        │ │
   │ │ idea_signals (timeseries — favorers/views)    │ │
   │ │ idea_to_listing (idea_id, listing_id) PK      │ │
   │ └──────────────────────────────────────────────┘ │
   └──────────────────────────────────────────────────┘
                                    │
            ┌───────────────────────┼─────────────────────┐
            ▼                       ▼                     ▼
   ┌───────────────────┐  ┌──────────────────┐  ┌────────────────────┐
   │ POST /extension/  │  │ /admin/keywords  │  │ /admin/ideas/{id}/ │
   │ idea (passive log │  │ /admin/ideas     │  │ create-listing →   │
   │ from extension v3 │  │ velocity sort    │  │ wizard 3-step →    │
   │ reference mode)   │  │ status filters   │  │ listing_creator    │
   └───────────────────┘  └──────────────────┘  └────────────────────┘
                                                         │
                                                         ▼
                                                   ┌──────────────┐
                                                   │ Etsy draft   │
                                                   │ + idea_to_   │
                                                   │ listing link │
                                                   │ + status=    │
                                                   │   drafted    │
                                                   └──────────────┘
```

**4-layer idea schema:**
- `keywords` — user-managed search terms; `enabled` gates miner; `last_run_at` for ops visibility
- `ideas` — `UNIQUE(source, source_listing_id)` deduplicates; `status` ∈ {new, saved, drafted, dismissed}; carries `reference_image_url`, `tags_json`, `price_cents`, taxonomy fields
- `idea_signals` — append-only timeseries `(idea_id, captured_at, num_favorers, views_all_time)`; velocity computed via `velocity_per_day(idea_id)` query
- `idea_to_listing` — composite-PK provenance link; populated on wizard submit

**Wizard stateless:** idea_id in URL only; each step recomputes prefill — no server session.

**v0.10 — 2-phase flow:** wizard submit now calls `listing_creator_service.save_draft()` (renders composites + writes a `Listing` row with `status='new'`, `etsy_listing_id=NULL`) then **redirects 303** to `/admin/listings/{id}`. The detail page renders the composite gallery and an edit form; the user reviews, edits, then clicks **Upload** to trigger `upload_to_etsy()` (CAS-locked Etsy push). Sync (Etsy → local), live Edit (write-through to Etsy), and Delete (Etsy DELETE + soft-delete) are wired through the same detail page.

### Listing lifecycle (v0.10)

```
   ┌──── save_draft() ──── new ──── upload_to_etsy() (CAS) ──── uploading
   │                       │                                          │
   │   user edits locally  │                                       success
   │   (PUT, no Etsy call) │                                          ▼
   │                       │                                       created  ←──── sync (GET) ←──── Etsy
   │                       │                                          │
   │                       ▼                                       PUT (live, write-through to Etsy)
   │                    failed  ←──── upload error (rollback) ────────┤
   │                       │                                          │
   └──── retry ────────────┘                                       DELETE
                                                                      │
                                                                      ▼
                                                                   deleted (soft, Etsy=inactive)
```

## Status State Machine

```mermaid
stateDiagram-v2
    [*] --> new : POST /ingest
    new --> title_processing : title_optimizer picks up
    title_processing --> mockup_pending : 3 variants generated
    title_processing --> failed : Claude error / Etsy API error
    mockup_pending --> mockup_processing : mockup_pipeline picks up
    mockup_processing --> review : 3 mockups generated + R2 uploaded
    mockup_processing --> failed : remove.bg / Imagen error
    review --> review : waiting for Notion sync (notion_page_id=NULL)
    review --> approved : pull_approvals detects Approved in Notion
    approved --> pushing : etsy_uploader picks up
    pushing --> pushed : Etsy PATCH + image upload success
    pushing --> failed : Etsy API error (retriable with push_attempts counter)
    failed --> new : manual reset (POST /admin/reset or re-ingest)
    pushed --> [*]
```

## Database Schema

```mermaid
erDiagram
    LISTINGS {
        int id PK
        str etsy_listing_id UK "nullable v0.10"
        str original_title
        text original_desc
        text original_tags
        text original_images
        str status "new|uploading|created|syncing|failed|deleted"
        str notion_page_id
        int push_attempts
        text last_push_error
        datetime pushed_at
        int template_id
        int design_id
        text local_payload_json "v0.10 editable payload"
        datetime deleted_at "v0.10 soft-delete"
        datetime created_at
        datetime updated_at
    }

    TITLE_VARIANTS {
        int id PK
        int listing_id FK
        int variant_number
        str title
        text rationale
        text tags
        bool selected
        datetime created_at
    }

    MOCKUP_VARIANTS {
        int id PK
        int listing_id FK
        int variant_number
        str r2_key
        str public_url
        bool selected
        datetime created_at
    }

    JOBS {
        int id PK
        int listing_id FK
        str type
        str status
        text error_log
        datetime started_at
        datetime finished_at
    }

    API_CREDENTIALS {
        int id PK
        str provider
        text access_token
        text refresh_token
        datetime expires_at
        datetime created_at
        datetime updated_at
    }

    TEMPLATES {
        int id PK
        str name
        str category
        str base_image_url
        text composite_anchor_json
        int default_price_cents
        text variation_options_json
        text color_base_images_json
        datetime created_at
        datetime updated_at
    }

    TEMPLATE_IMAGES {
        int id PK
        int template_id FK
        str image_url
        str color "nullable"
        int rank
        str anchor_json
        str role
        datetime created_at
    }

    TEMPLATE_VARIATIONS {
        int id PK
        int template_id FK
        str size
        str color
        int price_cents
        str sku "nullable"
        datetime created_at
    }

    DESIGNS {
        int id PK
        str name
        str source_type
        str file_url
        int width
        int height
        datetime created_at
    }

    LISTINGS ||--o{ TITLE_VARIANTS : "has"
    LISTINGS ||--o{ MOCKUP_VARIANTS : "has"
    LISTINGS ||--o{ JOBS : "has"
    TEMPLATES ||--o{ TEMPLATE_IMAGES : "contains"
    TEMPLATES ||--o{ TEMPLATE_VARIATIONS : "defines"
    TEMPLATES ||--|{ DESIGNS : "uses"
```

## Directory Structure

```
etsyauto/
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── main.py             # App entrypoint, lifespan, CORS, routers
│   │   ├── config.py           # Pydantic Settings (loads .env)
│   │   ├── database.py         # SQLAlchemy engine + session factory
│   │   ├── scheduler.py        # APScheduler singleton + job registration
│   │   ├── routes/
│   │   │   ├── health.py       # GET /health
│   │   │   ├── ingest.py       # POST /ingest
│   │   │   ├── etsy_auth.py    # GET /auth/etsy/start|callback|status
│   │   │   └── admin.py        # POST /admin/run-uploader
│   │   ├── models/
│   │   │   ├── listing.py
│   │   │   ├── title_variant.py
│   │   │   ├── mockup_variant.py
│   │   │   ├── job.py
│   │   │   └── api_credential.py
│   │   ├── workers/
│   │   │   ├── title_optimizer.py   # Claude title generation
│   │   │   ├── mockup_pipeline.py   # remove.bg + Imagen + R2
│   │   │   ├── notion_sync.py       # sync_to_notion + pull_approvals
│   │   │   └── etsy_uploader.py     # Push to Etsy + retry
│   │   ├── services/
│   │   │   ├── listing_service.py   # DB queries + status transitions
│   │   │   ├── image_service.py     # PIL image utilities
│   │   │   ├── review_service.py    # Notion review logic
│   │   │   └── retry_policy.py      # Exponential backoff helpers
│   │   ├── clients/
│   │   │   ├── claude_client.py
│   │   │   ├── etsy_api_client.py
│   │   │   ├── etsy_oauth.py        # PKCE flow
│   │   │   ├── gemini_imagen_client.py
│   │   │   ├── notion_client.py
│   │   │   ├── r2_storage_client.py
│   │   │   └── removebg_client.py
│   │   └── prompts/                 # Claude system/user prompt templates
│   ├── alembic/                     # DB migrations
│   ├── tests/                       # pytest unit tests (78 tests)
│   ├── pyproject.toml
│   └── .env                         # (git-ignored) API keys
├── extension/                  # Chrome MV3 extension
│   ├── manifest.json           # MV3 manifest
│   ├── background/
│   │   └── service-worker.js   # Background service worker
│   ├── side-panel/
│   │   ├── side-panel.html
│   │   ├── side-panel.js
│   │   └── side-panel.css
│   ├── content-scripts/
│   │   ├── listing-detector.js
│   │   └── listing-detector.css
│   ├── shared/                  # Shared utilities
│   └── icons/
├── docs/                        # Project documentation
├── scripts/
│   ├── smoke-test-e2e.sh        # Structural readiness smoke test
│   └── seed-test-listing.py     # Insert fake listing for debugging
└── README.md
```

## Scheduler Job Timing

| Job | Interval | Trigger Condition | Max Instances |
|-----|----------|-------------------|---------------|
| `title_optimizer` | 30s | `status = 'new'` | 1 |
| `sync_to_notion` | 30s | `status = 'review'` AND `notion_page_id IS NULL` | 1 |
| `pull_approvals` | 60s | Notion query for `status = 'Approved'` | 1 |
| `mockup_pipeline` | 60s | `status = 'mockup-pending'` | 1 |
| `etsy_uploader` | 600s | `status = 'approved'` | 1 |

## Security Boundaries

- All API keys stored in `backend/.env` (never committed to git)
- Admin endpoints protected by `X-Admin-Token` header
- Etsy OAuth uses PKCE (no client_secret transmitted during token exchange)
- CORS restricted to `chrome-extension://*` and `localhost` origins only
- R2 bucket: public read for image embeds; write requires scoped API token
- Notion: Internal Integration token — access scoped to shared database only
