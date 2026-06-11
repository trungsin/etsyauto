# Self-Hosted Background Removal Ship

**Date**: 2026-06-11 13:30
**Severity**: Medium (infrastructure shift, no user-facing regression)
**Component**: Background removal pipeline, image processing
**Status**: Completed

## What Happened

PhotoRoom API (402 plan-inactive) and remove.bg (402 quota-exceeded) both dead. Pivoted to local rembg as PRIMARY provider. 3-phase implementation: environment config, client chain refactor, E2E testing + code review.

## The Brutal Truth

Dead API keys created 15 minutes of confusion—thought quota logic was wrong, wasn't. Real issue: vendor lock-in made us blind. Local model was always viable. Cost flipped from $X/image to $0. Latency trade-off: 37-100s per inference on 6-core CPU (vs 2-3s API calls) exposed async bottleneck in extract_design route that would freeze the entire server during cutout. Code review caught it. That alone justified the whole refactor.

## Technical Details

**Model**: birefnet-general (benchmark winner, 37s/img, MIT license, 930MB, thread-safe via module-level Lock on session creation + inference).

**Chain**: RembgClient → PhotoRoomClient → RemoveBgClient. APIs only used if keys set (graceful degradation). Deleted duplicate fallback logic in artwork_service (DRY violation).

**Critical fix**: extract_design route async context was blocking on ~37-100s rembg call. Wrapped in `run_in_threadpool()` → event loop stays responsive. artwork_admin /removebg handler added broad exception handler (500 → 502) for unhandled rembg failures in keyless setup.

**Warmup**: Daemon thread in lifespan, ~100s cold-start mitigation (Cloudflare 524 risk pre-warmup). Skipped under pytest to keep test suite fast. Manual E2E: warmup 14.3s warm-disk, /health 29ms during warmup, cutout visually clean.

**Test coverage**: 12 new tests (no model download), full suite 20 failed/509 passed (vs baseline 21/497). Zero new failures. One baseline failure FIXED (composite preview test no longer raises ValueError when no API keys).

## What We Tried

1. Considered isnet-general-use (faster ~25s) — rejected for quality loss.
2. Considered bria-rmbg — rejected for non-commercial license.
3. Started with bespoke rembg fallback in artwork_service → DRY violation, refactored into chain.
4. Code review found 2 issues; both fixed same day (extract_design async block, artwork_admin error handling).

## Root Cause Analysis

**Primary**: API vendor lock-in masked viable local alternative. No fallback strategy cost us visibility into cost/latency trade-offs.

**Secondary**: extract_design async context wasn't designed for >30s inference. Route handler blocked event loop synchronously — codebase assumed quick API roundtrips.

**Tertiary**: No error handling in artwork_admin for rembg-only scenarios (keyless setup). Returned 500 instead of graceful degradation.

## Lessons Learned

1. **Local-first mentality**: When API costs climb or quotas fail, local inference (especially deep learning) is often worth latency cost if you own infra.

2. **Thread safety at module level**: rembg session creation isn't thread-safe. Lock at module load time, not per-call. Serialized inference acceptable for single-core-like behavior on 6-core saturated CPU.

3. **Async/sync boundaries**: Routes async by default in FastAPI. Any blocking I/O >1s needs `run_in_threadpool()`. extract_design was ticking time-bomb.

4. **Error handling in graceful degradation**: When APIs demoted to fallback, error paths must still return sensible output (202 + queued job) not 500s.

5. **Warmup trade-offs**: Cold-start 100s is real. Warmup daemon buys ~14s on warm-disk. Skip under test. Document.

## Next Steps

- Monitor warmup behavior in production (check /health 502s during boot).
- Watch rembg inference timing under load (6-core saturation → queuing needed if traffic spikes).
- Consider GPU-accelerated rembg if image volume scales (CUDA version, model caching strategies).
- Docs: update README with local model bandwidth expectations.

**Files Modified**: backend/app/config.py, clients/rembg_client.py, clients/bg_removal.py, services/artwork_service.py, main.py, routes/idea_wizard.py, routes/artwork_admin.py, .env.example, tests/test_bg_removal_chain.py, docs/project-changelog.md, README.md.

---

**Status**: DONE
**Summary**: Migrated dead-API background removal to local rembg primary, fixed extract_design async bottleneck, zero new test failures, one baseline fixed.
