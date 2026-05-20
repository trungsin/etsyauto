# Cloden Design POD Artwork Pipeline Implementation

**Date**: 2026-05-20 14:38  
**Severity**: Medium  
**Component**: Backend artwork processing, Admin UI  
**Status**: Resolved

## What Happened

Completed 4-step interactive admin pipeline converting mockup images into POD-ready artwork. Users upload mockups, crop via canvas UI, refine backgrounds with GPT-Image-1, remove backgrounds, then upscale 4× using Real-ESRGAN. All steps exposed as individual API routes with step-by-step approval model.

## The Brutal Truth

Code review caught **five issues** before merge—two critical enough to crash production in specific deployment configs. The worst part: the relative URL bug (`/static/…`) would silently break for any deployment NOT using R2 cloud storage, affecting the entire refine + removebg pipeline. This would've hit our self-hosted environments hard and been a nightmare to debug without logs.

Real-ESRGAN dependency handling is awkward: 1.5GB of trained weights we can't include in pyproject.toml, so it requires manual installation and lazy imports with defensive error handling. Works, but fragile and not obvious to future maintainers.

## Technical Details

### New Files
- `backend/app/models/artwork.py` — Artwork ORM model, 6-state lifecycle (pending→cropped→refined→removebg_done→upscaling→done|failed)
- `backend/app/clients/openai_imagen_client.py` — GPT-Image-1 edit API wrapper (httpx), sends base64 image + mask
- `backend/app/services/artwork_service.py` — Pipeline orchestration, Real-ESRGAN CPU upscale via subprocess
- `backend/app/routes/artwork_admin.py` — 6 FastAPI routes (POST upload, POST crop, POST refine, POST removebg, POST upscale, GET status/preview)
- `backend/app/templates/artwork/index.html` — Multi-step Jinja2 UI with canvas crop (HTML5 canvas, drag-select, fractional coords), 5s polling loop for task status
- `backend/alembic/versions/81fe2ec1dc2b_add_artworks_table.py` — Migration adding `artworks` table

### Critical Bug: Relative URL Crash
**Found in code review.** Route was passing `/static/uploads/…` URL directly to httpx POST body for GPT-Image-1 API. On non-R2 deployments, this fails—the endpoint needs **bytes or base64 data**, not a relative filesystem path.

**Fix**: Added `_load_image_bytes()` helper that detects `/static/…` URLs, reads from disk, and returns bytes. Backends configured with R2 skip the read (S3 handles URL resolution internally).

**Impact**: Without this, refine + removebg routes return 400+ errors, breaking the entire pipeline for self-hosted users.

### High Priority: Missing Route Auth
GET `/artwork/` and POST `/artwork/upload` had no role/permission checks. Any user could hit the admin page and start processing images (though they couldn't see others' results without guessing IDs).

**Fix**: Added `@require_role("admin")` decorator to all 6 artwork routes.

### High Priority: Unhandled HTTP Errors
Refine and removebg routes didn't catch `httpx.HTTPError` exceptions—GPT-Image-1 or Remove.bg API failures would crash with 500s instead of returning graceful 400/503 responses.

**Fix**: Wrapped API calls in try-catch, return `{"error": str(e), "status": "failed"}` on failure, update artwork status to `failed`.

### Medium Priority: Upscale Race Condition
If upscale route hit twice rapidly (e.g., user double-clicks button), both BackgroundTasks would execute Real-ESRGAN simultaneously, consuming CPU and disk space.

**Fix**: Set `status="upscaling"` in the route handler **before** spawning BackgroundTask, so second request sees status already changing and returns early.

### Low Priority: Duplicate Import
`_upscale_realesrgan()` had `import numpy` at function top + already at module level. Removed duplicate.

## What We Tried

1. **Batch async approach** → Rejected. Users need per-step approval before expensive API calls (GPT edits cost $$, upscale takes 2-4 min). Sequential UI flow ensures control.

2. **Include Real-ESRGAN in pyproject.toml** → Rejected. Model weights alone are 1.5GB; would bloat Docker images unacceptably. Lazy import + clear error message acceptable trade-off.

3. **S3 URL passthrough** → Rejected. GPT-Image-1 and Remove.bg need actual image bytes or reachable HTTP URLs. Relative `/static/…` paths don't work; detection + disk read is simplest solution.

## Root Cause Analysis

**Why the URL bug slipped through initial implementation:**
- Developer assumed all deployments use R2 (true for staging/prod, false for local dev + self-hosted)
- No explicit test for non-R2 image loading paths
- httpx silently accepts string URLs; only backend API rejects them

**Why auth was missing:**
- Routes added late in sprint; copy-paste from unauthenticated preview endpoints
- No PR template check for auth decorators

**Why HTTP errors weren't caught:**
- httpx docs emphasize auto-retry for transient errors; developer assumed only `.raise_for_status()` explicitly needed
- Actual failure modes (quotas, blocked IPs, invalid tokens) still crash without catch

**Why race condition existed:**
- BackgroundTask is fire-and-forget; no idempotency guard on task ID or status check before spawn
- Fast clickers could execute twice

## Lessons Learned

1. **Multi-deployment assumption kills self-hosted paths.** Always test both "cloud" (S3/R2) and "local" (filesystem) image backends before merging. The `/static/` URL bug would've caused silent failures in production.

2. **Auth decorators must be non-negotiable.** Add to PR checklist: "All routes have explicit auth protection, document in docstring if intentionally public."

3. **External API calls need defensive error handling.** Don't assume GPT-Image-1, Remove.bg, Real-ESRGAN will always succeed. Quotas exhaust, IPs get blocked, tokens expire. Catch broad `HTTPError`, log details, return user-friendly error.

4. **Race conditions on background tasks require explicit state guards.** Setting status **before** task spawn (not after) is simpler than task idempotency keys. One state check beats two.

5. **Large ML model dependencies are a deployment headache.** Document manual install steps clearly. Consider lazy import + graceful error if weights missing, so service starts but routes fail cleanly instead of boot crash.

## Next Steps

- [ ] Add integration test: full pipeline (upload → crop → refine → removebg → upscale) with mock APIs
- [ ] Document Real-ESRGAN setup in `deployment-guide.md`: manual install, expected RAM, timeout tuning
- [ ] Add monitoring: track API failure rates (GPT quota, Remove.bg blocks), upscale duration percentiles
- [ ] Consider caching refined images (GPT-1 edits are expensive; same mockup + crop shouldn't edit twice)
- [ ] Review all new routes for similar multi-deployment bugs (S3 vs filesystem, auth, error handling)

**Owner**: Code review caught these; implementation team has fixes merged. No blocking issues remain.
