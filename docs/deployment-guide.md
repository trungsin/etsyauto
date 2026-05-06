# Deployment Guide

Setup EtsyAuto from scratch on a fresh Linux or macOS machine. Target: running in under 30 minutes.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | `pyenv install 3.12` or system package manager |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Chrome / Chromium | 120+ | Standard browser install |
| sqlite3 | any | Pre-installed on macOS/Linux |
| git | any | Pre-installed or package manager |

## Step 1 — Clone Repository

```bash
git clone <repo-url> etsyauto
cd etsyauto
```

## Step 2 — Install Python Dependencies

```bash
cd backend
uv sync
```

This creates `.venv/` and installs all dependencies from `pyproject.toml`.

## Step 3 — Obtain API Keys

You need accounts/keys from 6 services. Open each link, follow signup, copy key.

### 3a. Etsy Developer App
1. Go to https://www.etsy.com/developers/your-apps
2. Click **Create New App** — name it "EtsyAuto"
3. Set Redirect URI: `http://localhost:8787/auth/etsy/callback`
4. Copy **Keystring** → `ETSY_API_KEY`
5. Copy **Shared Secret** → `ETSY_SHARED_SECRET`

### 3b. Anthropic (Claude)
1. Go to https://console.anthropic.com/settings/keys
2. Create API key
3. Copy → `ANTHROPIC_API_KEY`

### 3c. remove.bg
1. Go to https://www.remove.bg/api
2. Sign up (free tier: 50 API credits/month)
3. Copy API key → `REMOVEBG_API_KEY`

### 3d. Google Gemini (Imagen)
1. Go to https://aistudio.google.com/app/apikey
2. Create API key
3. Copy → `GEMINI_API_KEY`

> **Note:** The current model ID is `gemini-2.5-flash-preview-05-20`.
> Switch to `gemini-2.5-flash-image` when it reaches GA status.

### 3e. Notion Integration
1. Go to https://www.notion.so/profile/integrations
2. Click **New integration** → name "EtsyAuto" → Submit
3. Copy **Internal Integration Token** → `NOTION_API_KEY`
4. Follow `docs/notion-db-setup.md` to create and share the review database
5. Copy database ID from URL → `NOTION_DATABASE_ID`

### 3f. Cloudflare R2
1. Log in to https://dash.cloudflare.com → **R2 Object Storage**
2. Create bucket: `etsyauto-mockups`
3. Enable **Public Access** on the bucket → note the public URL
4. **R2 API Tokens** → Create token with **Object Read & Write** on this bucket
5. Copy:
   - Account ID (from dashboard URL) → `R2_ACCOUNT_ID`
   - Access Key ID → `R2_ACCESS_KEY_ID`
   - Secret Access Key → `R2_SECRET_ACCESS_KEY`
   - Public URL (e.g. `https://pub-abc123.r2.dev`) → `R2_PUBLIC_URL`

## Step 4 — Configure Environment

```bash
# From backend/ directory
cp .env.example .env   # if example exists, else create new
```

Edit `backend/.env`:

```env
# Etsy OAuth
ETSY_API_KEY=your_etsy_keystring_here
ETSY_SHARED_SECRET=your_etsy_shared_secret_here
ETSY_REDIRECT_URI=http://localhost:8787/auth/etsy/callback
ETSY_SCOPE=listings_r listings_w

# AI providers
ANTHROPIC_API_KEY=sk-ant-...
REMOVEBG_API_KEY=your_removebg_key
GEMINI_API_KEY=your_gemini_key

# Notion
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Cloudflare R2
R2_ACCOUNT_ID=your_cf_account_id
R2_ACCESS_KEY_ID=your_r2_access_key
R2_SECRET_ACCESS_KEY=your_r2_secret_key
R2_BUCKET_NAME=etsyauto-mockups
R2_PUBLIC_URL=https://pub-abc123.r2.dev

# App internals (optional)
DATABASE_URL=sqlite:///./etsyauto.db
STATIC_DIR=./static
ADMIN_TOKEN=choose_a_random_secret_token
```

> **Security:** Never commit `.env` to git. It is listed in `.gitignore`.

## Step 5 — Run Database Migrations

```bash
cd backend
uv run alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial schema
INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> aa88c2baf005, add push tracking
INFO  [alembic.runtime.migration] Running upgrade aa88c2baf005 -> e7d4a402ca7b, add notion page id
```

Verify DB created:
```bash
sqlite3 etsyauto.db ".tables"
# Expected: api_credentials  jobs  listings  mockup_variants  title_variants
```

## Step 6 — Start the Backend

```bash
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

Expected startup output:
```
INFO  uvicorn.error: Application startup complete.
INFO  app.scheduler: Registered job: title_optimizer (interval=30s)
INFO  app.scheduler: Registered job: mockup_pipeline (interval=60s)
INFO  app.scheduler: Registered job: sync_to_notion (interval=30s)
INFO  app.scheduler: Registered job: pull_approvals (interval=60s)
INFO  app.scheduler: Registered job: etsy_uploader (interval=600s)
INFO  app.scheduler: Scheduler started
```

Verify health:
```bash
curl http://localhost:8787/health
# {"status":"ok","db":"ok","scheduler":"running"}
```

## Step 7 — Etsy OAuth (First-Time Authorization)

1. Open Chrome → navigate to: `http://localhost:8787/auth/etsy/start`
2. You will be redirected to Etsy's consent page
3. Log in with your Etsy seller account and click **Allow Access**
4. Etsy redirects to `http://localhost:8787/auth/etsy/callback?code=...`
5. Backend exchanges code for tokens and stores them in `api_credentials` table
6. Verify: `curl http://localhost:8787/auth/etsy/status`

Tokens auto-refresh — you only need to do this once. If tokens expire (> 1 hour idle + no refresh), repeat this step.

## Step 8 — Load Chrome Extension

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `extension/` folder from this repository
5. Note the Extension ID (shown under the extension card)
6. Pin the extension to toolbar

## Step 9 — Verify End-to-End

1. Navigate to any Etsy listing page (e.g., `https://www.etsy.com/listing/123456789/...`)
2. Click the EtsyAuto extension icon → Side panel opens
3. Click **Send to Optimizer**
4. Backend logs show: `Ingested listing 123456789 → job_id=1`
5. Wait ~30s → title variants generated (check `curl http://localhost:8787/health`)
6. Wait ~60s → mockup pipeline runs
7. Check Notion database → new review page appears with title variants + mockup images
8. In Notion: set **Selected Title**, **Selected Mockup**, set **Status → Approved**
9. Wait ~60s → `pull_approvals` detects approval
10. Wait up to 10min (or `curl -X POST http://localhost:8787/admin/run-uploader -H "X-Admin-Token: <your_token>"`)
11. Check Etsy listing — title and first image updated

## Step 10 — Run Smoke Test

```bash
bash scripts/smoke-test-e2e.sh
```

All checks should show `[PASS]`. See `scripts/smoke-test-e2e.sh` for what is validated.

## Inserting a Test Listing (Without Extension)

For debugging without a real Etsy listing:

```bash
cd backend && uv run python ../scripts/seed-test-listing.py
```

This inserts `etsy_listing_id=999999` with `status=new`. The scheduler picks it up within 30s.

To verify:
```bash
sqlite3 backend/etsyauto.db "SELECT id, etsy_listing_id, status FROM listings;"
```

## Running Tests

```bash
cd backend
uv run pytest -v
# Expected: 78 passed in <10s
```

## Auto-start on Boot (Optional, Linux systemd)

Create `/etc/systemd/system/etsyauto.service`:

```ini
[Unit]
Description=EtsyAuto Backend
After=network.target

[Service]
Type=simple
User=<your-username>
WorkingDirectory=/path/to/etsyauto/backend
ExecStart=/path/to/etsyauto/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable etsyauto
sudo systemctl start etsyauto
```

## Windows Notes

- Use WSL2 (Ubuntu) — all commands above work unchanged inside WSL2
- Chrome extension loads from `\\wsl$\Ubuntu\path\to\extension` — use UNC path in "Load unpacked"
- SQLite path in `.env` must use forward slashes: `sqlite:///./etsyauto.db`
- `uv` has a native Windows installer — see https://docs.astral.sh/uv/getting-started/installation/

## macOS Notes

- Homebrew: `brew install python@3.12` if needed
- `uv` installs to `~/.cargo/bin/uv` via the curl installer
- No special considerations — all commands identical to Linux
