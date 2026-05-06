# Troubleshooting Guide

Common errors and their fixes. Check backend logs first:
```bash
# Logs stream to stdout when running uvicorn
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787
```

---

## 1. "Backend disconnected" / Extension shows no connection

**Symptom:** Side panel shows "Cannot connect to backend" or fetch errors in extension console.

**Causes & Fixes:**

**A. uvicorn not running**
```bash
curl http://localhost:8787/health
# If connection refused → start backend:
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8787
```

**B. Wrong port**
- Backend must run on port `8787` (hardcoded in extension `manifest.json` host_permissions)
- Check: `lsof -i :8787` — something else may be using the port
- Fix: kill conflicting process or change port in both `backend/.env` and `extension/manifest.json`

**C. Extension not reloaded after backend restart**
- Go to `chrome://extensions/` → click the refresh icon on EtsyAuto
- Or: close and reopen the side panel

**D. CORS rejection (extension ID changed)**
- If you reinstalled the extension, its ID changed
- Extension ID is matched by regex `chrome-extension://.*` in backend CORS — this should always pass
- Check browser console for exact CORS error message

---

## 2. "Etsy 401 Unauthorized" / Listings not fetching

**Symptom:** Backend logs show `401` when calling Etsy API. Title optimizer worker fails.

**Cause:** OAuth access token expired (tokens expire after 1 hour; refresh token valid 90 days).

**Fix — refresh tokens:**
1. Open Chrome → `http://localhost:8787/auth/etsy/start`
2. Complete Etsy consent flow again
3. Check: `curl http://localhost:8787/auth/etsy/status` → should show `expires_in > 0`

**Fix — check token in DB:**
```bash
sqlite3 backend/etsyauto.db \
  "SELECT provider, expires_at, substr(access_token,1,20) FROM api_credentials;"
```

**If refresh token also expired (> 90 days):**
- Full re-auth required — repeat Step 7 of deployment guide

---

## 3. "Notion image not loading" / Mockup images show broken in Notion

**Symptom:** Notion review page created but images show broken/empty.

**Cause A: R2 public access not enabled**
- Go to Cloudflare R2 dashboard → bucket `etsyauto-mockups` → **Settings** → **Public Access** → Enable
- Re-generate mockups: reset listing status to `mockup-pending`, wait for pipeline

**Cause B: Wrong R2_PUBLIC_URL in .env**
```bash
# Test public URL directly:
curl -I "https://pub-YOURHASH.r2.dev/some_uploaded_file.png"
# Should return 200, not 403/404
```
- Verify `R2_PUBLIC_URL` in `.env` matches the actual public bucket URL (no trailing slash)

**Cause C: R2 upload failed silently**
```bash
sqlite3 backend/etsyauto.db \
  "SELECT id, r2_key, public_url FROM mockup_variants WHERE listing_id=<id>;"
```
- If `public_url` is NULL → upload failed; check backend logs for boto3 errors
- Common: incorrect `R2_ACCOUNT_ID` → fix in `.env` and re-run mockup pipeline

**Reset listing to re-run mockup pipeline:**
```bash
sqlite3 backend/etsyauto.db \
  "UPDATE listings SET status='mockup-pending' WHERE id=<id>;"
```

---

## 4. "Imagen quality bad" / Mockups look distorted or off-brand

**Symptom:** Generated mockup images have wrong product placement, distorted proportions, or irrelevant scenes.

**Fix A: Regenerate with different seed**
Reset the listing status to re-run the pipeline:
```bash
sqlite3 backend/etsyauto.db \
  "DELETE FROM mockup_variants WHERE listing_id=<id>;
   UPDATE listings SET status='mockup-pending' WHERE id=<id>;"
```

**Fix B: Adjust scene prompts in `gemini_imagen_client.py`**
- The scene prompt is constructed per-category — edit the prompt template
- Add negative prompts if Imagen supports them for your model version
- Try more specific scene descriptions: instead of "lifestyle scene", use "flat lay on white marble surface, natural light, minimalist"

**Fix C: Poor source image quality**
- remove.bg works best with high-contrast product-vs-background images
- Ensure original Etsy listing images are at least 600×600px
- If remove.bg fails, check `REMOVEBG_API_KEY` is valid and has credits remaining:
  ```bash
  curl "https://api.remove.bg/v1.0/account" \
    -H "X-Api-Key: YOUR_KEY"
  ```

**Fix D: Model version issue**
- Current model: `gemini-2.5-flash-preview-05-20`
- If this model is deprecated, switch to `gemini-2.5-flash-image` (GA version) in `clients/gemini_imagen_client.py`

---

## 5. "Listing stuck in status=processing" / Pipeline not advancing

**Symptom:** Listing stays in `title-processing` or `mockup-processing` for >5 minutes.

**Diagnosis:**
```bash
# Check current listing status
sqlite3 backend/etsyauto.db \
  "SELECT id, etsy_listing_id, status, updated_at FROM listings ORDER BY updated_at DESC LIMIT 5;"

# Check jobs table for errors
sqlite3 backend/etsyauto.db \
  "SELECT type, status, error_log, started_at, finished_at FROM jobs ORDER BY id DESC LIMIT 10;"
```

**Check scheduler is running:**
```bash
curl http://localhost:8787/health
# "scheduler": "running" must be true
```

**Check backend logs** for the relevant worker — errors are logged at WARNING/ERROR level.

**Common root causes:**

| Status Stuck | Likely Cause |
|---|---|
| `title-processing` | `ANTHROPIC_API_KEY` invalid or quota exceeded |
| `title-processing` | Etsy API 401 (token expired — see Error 2) |
| `mockup-processing` | `REMOVEBG_API_KEY` out of credits |
| `mockup-processing` | `GEMINI_API_KEY` invalid or Imagen API error |
| `mockup-processing` | R2 upload failing (check boto3 credentials) |
| `review` | `NOTION_API_KEY` invalid or database not shared with integration |
| `approved` | Etsy API 401 or listing not editable (sold, deleted) |

**Manual reset to retry:**
```bash
# Reset to 'new' to retry from title generation:
sqlite3 backend/etsyauto.db \
  "UPDATE listings SET status='new' WHERE id=<id>;"

# Reset to retry mockup only:
sqlite3 backend/etsyauto.db \
  "DELETE FROM mockup_variants WHERE listing_id=<id>;
   UPDATE listings SET status='mockup-pending' WHERE id=<id>;"
```

---

## 6. Notion approval not detected / Listing stays "review"

**Symptom:** You set Status=Approved in Notion but listing never advances to `approved` in SQLite.

**Checklist:**
1. Both **Selected Title** AND **Selected Mockup** must be set (not just Status)
2. Status value must be exactly `Approved` (capital A — Notion select is case-sensitive)
3. `pull_approvals` runs every 60s — wait at least 90s after setting approval

**Verify Notion DB properties exist:**
```bash
curl http://localhost:8787/health
# Backend logs on startup show Notion schema validation result
```

If schema validation failed → re-check `docs/notion-db-setup.md` property names (exact spelling required).

**Manually trigger approval pull:**
```bash
curl -X POST http://localhost:8787/admin/run-uploader \
  -H "X-Admin-Token: <your_admin_token>"
```

---

## 7. Tests failing after code changes

```bash
cd backend && uv run pytest -v --tb=short
```

**Common failures:**

- `ImportError` → run `uv sync` first
- `alembic.util.exc.CommandError` → run `uv run alembic upgrade head`
- DB fixture errors → delete `etsyauto.db` and re-migrate (tests use in-memory DB, so this affects manual testing only)
- Mock not patching correctly → check `@patch` decorator path matches actual import path in the module under test

---

## 8. "uv: command not found"

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # or ~/.zshrc
```

---

## 9. Extension not detecting listing page

**Symptom:** Side panel shows no listing info on an Etsy listing page.

**Check:**
- URL must match `https://www.etsy.com/listing/*` exactly
- Content script may not have injected — try hard refresh (Ctrl+Shift+R)
- Open DevTools → Console tab → look for `[etsyauto]` log messages
- If no messages: go to `chrome://extensions/` → EtsyAuto → inspect content scripts

---

## Getting Help

1. Check backend logs (stdout where uvicorn runs)
2. Check Chrome DevTools console (F12 on Etsy page; inspect service worker at `chrome://serviceworker-internals/`)
3. Check SQLite state: `sqlite3 backend/etsyauto.db ".dump listings"` (truncate large text fields)
4. Run smoke test: `bash scripts/smoke-test-e2e.sh`
