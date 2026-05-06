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
        str etsy_listing_id UK
        str original_title
        text original_desc
        text original_tags
        text original_images
        str status
        str notion_page_id
        int push_attempts
        text last_push_error
        datetime pushed_at
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

    LISTINGS ||--o{ TITLE_VARIANTS : "has"
    LISTINGS ||--o{ MOCKUP_VARIANTS : "has"
    LISTINGS ||--o{ JOBS : "has"
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
