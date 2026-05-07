# EtsyAuto

EtsyAuto is a local-first Etsy listing optimizer that automates the mechanical work of SEO title generation and lifestyle mockup image creation while keeping the seller in full control. A Chrome MV3 extension captures listings directly from the Etsy seller dashboard, a FastAPI backend generates 3 AI-powered title variants (Anthropic Claude Sonnet 4.6) and 3 lifestyle mockup image variants (remove.bg + Google Gemini Imagen), surfaces them in a Notion review page for human approval, then pushes the approved content back to Etsy via the official API — all running locally with no SaaS subscription required.

> **Etsy ToS notice:** This tool uses only the official Etsy Open API v3. The Chrome extension reads listing data the seller sees in their own browser session. Automated bulk listing modification without seller review violates Etsy ToS — EtsyAuto enforces a mandatory human approval step before every Etsy update.

---

## Architecture

```
Chrome Extension → POST /ingest → FastAPI :8787 → SQLite
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
             title_optimizer     mockup_pipeline      etsy_uploader
             (Claude Sonnet)   (remove.bg + Imagen)  (Etsy API v3)
                    │                   │
                    └─────────┬─────────┘
                              ▼
                     Notion review page
                     (human approves)
```

Full component diagram, sequence diagram, state machine, and DB schema: [docs/system-architecture.md](docs/system-architecture.md)

---

## Quickstart

### Prerequisites

- Python 3.12+, `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Chrome 120+
- API keys for: Etsy, Anthropic, remove.bg, Google Gemini, Notion, Cloudflare R2

### Setup (10 commands)

```bash
# 1. Clone and enter repo
git clone <repo-url> etsyauto && cd etsyauto

# 2. Install Python dependencies
cd backend && uv sync

# 3. Copy and fill in environment variables
cp .env.example .env   # then edit .env with your API keys

# 4. Run database migrations
uv run alembic upgrade head

# 5. Start backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload

# 6. (New terminal) Verify backend health
curl http://localhost:8787/health
# → {"status":"ok","db":"ok","scheduler":"running"}

# 7. Authorize Etsy (first time only — opens in browser)
open http://localhost:8787/auth/etsy/start

# 8. Load Chrome extension
# chrome://extensions/ → Developer mode → Load unpacked → select extension/

# 9. Run smoke test (optional)
cd .. && bash scripts/smoke-test-e2e.sh

# 10. Seed a test listing (optional, no real Etsy account needed)
cd backend && uv run python ../scripts/seed-test-listing.py
```

Full setup guide with all API key registration steps: [docs/deployment-guide.md](docs/deployment-guide.md)

---

## Usage

1. Navigate to any Etsy listing in your seller account
2. Click the EtsyAuto extension icon → side panel opens → **Send to Optimizer**
3. Backend generates title variants (~30s) and mockup images (~60s)
4. A Notion page appears in your review database with all variants
5. In Notion: select **Selected Title**, **Selected Mockup**, set **Status → Approved**
6. Backend detects approval (~60s) and pushes to Etsy (within 10 minutes, or trigger manually):
   ```bash
   curl -X POST http://localhost:8787/admin/run-uploader \
     -H "X-Admin-Token: <your_admin_token>"
   ```

---

## Cost Per Listing

| Service | Typical Cost |
|---------|-------------|
| Anthropic Claude Sonnet 4.6 | ~$0.02 |
| remove.bg | $0.09–$0.20 |
| Google Gemini Imagen | ~$0.01–$0.05 |
| Cloudflare R2 | < $0.001 |
| **Total** | **< $0.30 typical, < $0.50 cap** |

---

## Tech Stack

FastAPI · SQLite · APScheduler · Anthropic Claude Sonnet 4.6 · remove.bg · Google Gemini Imagen · Notion API · Cloudflare R2 · Etsy Open API v3 · Chrome MV3 · uv

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Project Overview (PDR)](docs/project-overview-pdr.md) | Problem statement, decisions, cost breakdown |
| [System Architecture](docs/system-architecture.md) | Diagrams: components, sequence, state machine, DB schema |
| [Template System Guide](docs/template-system-guide.md) | Setup, admin UI walkthrough, API reference, anchor convention, troubleshooting |
| [Reference Workflow Guide](docs/reference-workflow-guide.md) | Capture Etsy public listings → AI suggest title → BG cutout → save to Notion Idea Bank |
| [Etsy Listing Creator Guide](docs/etsy-listing-creator-guide.md) | End-to-end: per-color mockups → variations matrix → Etsy draft listing (v0.4.0) |
| [Deployment Guide](docs/deployment-guide.md) | Full setup from scratch, API key registration |
| [Notion DB Setup](docs/notion-db-setup.md) | Notion database creation and review workflow |
| [Notion Idea Bank Setup](docs/notion-idea-bank-setup.md) | Idea Bank database creation, properties, integration share, data_source_id |
| [Troubleshooting](docs/troubleshooting.md) | 9 common errors with diagnosis and fixes |
| [Code Standards](docs/code-standards.md) | Python + JS conventions, testing rules |
| [Codebase Summary](docs/codebase-summary.md) | Module index with line counts |
| [Roadmap](docs/development-roadmap.md) | Phase history + post-MVP backlog |
| [Changelog](docs/project-changelog.md) | Version history |

---

## Running Tests

```bash
cd backend && uv run pytest -v
# 233 tests, < 130 seconds
```

---

## License

MIT. See LICENSE file.

> This software is provided as-is. The author is not affiliated with Etsy Inc. Use of the Etsy API is subject to [Etsy's API Terms of Use](https://www.etsy.com/legal/api). Sellers are responsible for ensuring their use complies with Etsy's policies.
