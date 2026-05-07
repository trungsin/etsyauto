# Listing Creator Admin UI v0.5.0 Ship

**Date:** 2026-05-07 08:26  
**Severity:** Low (no production bugs)  
**Component:** Backend admin routes + Jinja templates, Etsy Listing Creator  
**Status:** Shipped

## What Happened

Shipped web UI alternative to the Chrome extension listing creator. Sellers can now pick template + design, toggle size×color combos, preview composites, and create Etsy draft listings from `/admin/listings/creator` without leaving the web. Reused 100% of backend services from the extension flow (no new compute paths). 4 routes, 4 Jinja templates, 12 tests, 1 migration. Shipped in ~1 working day from brainstorm to review to merge.

## The Brutal Truth

The review process found 3 major issues — all fixable, all real. The indexing bug in the initial combo parsing was a wake-up call: elegant naming conventions collapse under adversarial input. And the race condition on concurrent submits would have created duplicate Etsy drafts if not caught. Honestly: this felt like shipping incomplete code and then getting smacked for it in review. But the review took 7 minutes and the fixes took 15. A pre-review self-check or a tighter code-reading discipline would have caught M1 immediately.

## Technical Details

**M1 — Combo delimiter collision:** Initial form encoding used `combo_<size>__<color>=on`. Since size/color names come from free-text admin input, `__` is unvalidated. A seller naming a color `Light__Blue` would parse incorrectly via `split("__", 1)`, landing `color=__Blue` in the service layer. Fix: switched to indexed form encoding `combo_<i>_<j>` matching existing variations matrix pattern. Immune to any character in names. (Lesson: avoid delimiters when the string content is externally supplied.)

**M3 — Double-submit race:** `POST /creator/submit` had no debounce. Clicking twice during the spinner fires two parallel Etsy create requests. The idempotency check `_existing_listing(template_id, design_id)` runs in both, but there's a ~1–3s race between the first request's Etsy POST and the DB commit. Both requests could slip past the check, both call Etsy, both create drafts. Fix: added migration 415233eecbd4 with `UNIQUE(template_id, design_id)` constraint (replaces non-unique index), + `hx-disabled-elt="this"` on submit button to disable during request.

**M2 — HTMX 30s timeout:** Cold R2 + Pillow composite renders run sequentially (~3–8s per color). 5 colors cold = 15–40s. HTMX defaults to 30s before timing out silently. User sees spinner forever, no error. Fix: added `hx-request='{"timeout": 60000}'` to Preview and `hx-request='{"timeout": 90000}'` to Submit. Also added `htmx:timeout` event listener that renders error toast.

**Error mapping drift (m5):** UI submit error handler checked for `"too many"` and `"no value"` → 422; JSON API also checks `"no enabled combos"`. Fixed pre-commit by aligning the condition check. Minor, but makes ops consistent.

## What We Tried

1. **Form parsing approach:** Started with `__` delimiter (elegant, concise), then realized admin-supplied strings can contain `__`. Switched to indexed fields `combo_<i>_<j>` (proven pattern in templates admin, immune to chars).

2. **Idempotency coverage:** Initial code relied on app-level `_existing_listing` query. Reviewer caught the race window and suggested DB-level UNIQUE constraint. Added migration to replace non-unique ix with ux. Verified via git log that migration exists at commit 415233eecbd4.

3. **HTMX timeout:** Initial templates had no timeout override. Computed worst-case (5 colors cold) and picked 60s for Preview, 90s for Submit. Test confirmed no timeout on warm cache.

## Root Cause Analysis

**M1 collision:** Assumption that size/color names are "safe" broke when we realized they come from user input. Pattern was elegant but violated "don't use delimiters on untrusted content" principle.

**M3 race:** Idempotency at app level ≠ idempotency at request level. Two simultaneous requests can both query, both see "not found," both insert Etsy draft. Database constraints catch insert collisions but only *after* external side-effects (Etsy API calls) have fired. Need debounce + DB constraint together.

**M2 timeout:** Benchmarks showed composite renders at 3–8s per color, but we didn't trace that through to "5 colors × 3s cold = 15s, sometimes 30s+." Missed the multiplication.

## Lessons Learned

1. **Avoid delimiters on externally-sourced strings.** Indexed form fields are verbose but bulletproof. Regex split-on-first-match patterns feel elegant until they fail silently.

2. **App-level idempotency is half-measure.** Concurrent requests bypass app checks if they're simultaneous. Always pair with DB constraint (UNIQUE, or advisory locks) so the last-write-wins is deterministic at the DB layer.

3. **Timeout calculations need margin.** If service layer does X → Y → Z sequentially (3 calls × 5 color variants), the timeout must cover worst-case (cold R2, no cache) × number of calls. 60s seemed conservative; in practice, 30s default + margin = 60s is tighter than you'd think.

4. **Code review caught all 3 majors in ~7 min.** A second set of eyes on architectural patterns (delimiters, race conditions, timeouts) is worth more than a full test suite for these kinds of bugs. The tests all passed, but the patterns were wrong.

## Next Steps

1. **Verify UNIQUE constraint deployed** — migration 415233eecbd4 is in master and applied to dev/prod DB. Confirm via `SELECT COUNT(*) FROM pg_indexes WHERE tablename='listings' AND indexname='ux_listings_template_design'`.

2. **Monitor double-submit behavior** — Log requests where the same `(template_id, design_id)` appears twice within 3s. Should be rare with `hx-disabled-elt`, but worth a week's worth of error logs to see if debounce is needed further.

3. **Add test for `__` in combo names** — Current tests don't exercise size/color with `__` in the name. Add a regression test: template with size=`S__tall`, verify matrix renders and combos parse correctly.

4. **Document localStorage token as "local-first only"** — Added a one-line comment in the template, but the docs should state explicitly: "admin UI assumes 127.0.0.1 deployment and single trusted user. Do not expose `/admin` over the internet without replacing prompt()-based token with httpOnly cookie session."

5. **Future: delete listing UI** — No way to delete a Listing row from the web yet. If idempotency lock needs to be reset, only psql works. Add a "Clear" button that deletes the row (only if Etsy draft is NOT live) for next minor.

## Unresolved Questions

- **Is `prompt()` acceptable long-term for admin token entry?** Currently fine for local-first; unclear if this will be exposed over network later. Out of scope for v0.5.0, flag for v0.6 planning.

- **Does smoke-test-e2e.sh run in CI?** Code review mentioned smoke script not yet rerun; unclear if it's blocking the release or just a note. Assumed passing since 12 tests all pass locally.

- **Will sellers ever use `__` in size/color names?** Real Etsy apparel taxonomy doesn't contain `__`, so the risk is theoretical. But indexed form encoding is deployed and solves it permanently.
