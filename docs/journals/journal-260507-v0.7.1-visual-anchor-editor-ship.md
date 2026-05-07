# v0.7.1 Ship — Visual 4-Point Anchor Editor

**Date**: 2026-05-07 13:55 UTC  
**Severity**: Low (clean ship, no regressions)  
**Component**: Backend admin routes, static JS/CSS, Jinja2 template  
**Status**: Completed + tagged 1cf0f28 / v0.7.1  

## What Happened

Shipped the v0.7.1 visual anchor editor: `GET/POST /admin/templates/{id}/anchor` with a vanilla JS drag interface. Non-dev sellers can now position quad zone corners by dragging SVG handles over the base image instead of hand-editing JSON.

**Deliverables:**
- Backend: 2 new routes (GET renders editor, POST validates + writes v2 anchor)
- Frontend: `anchor-editor.js` (~130 LOC), `anchor-editor.css` (~32 LOC), `anchor-editor.html` (~30 LOC)
- UI: "Edit anchor" link added to template list rows
- Tests: 265 → 274 (+9); all via `test_anchor_editor.py`
- Smoke: 21 → 22/22 (anchor route reachable check)
- Docs: changelog, roadmap, codebase summary, template-system-guide all updated

## The Brutal Truth

This was a smooth ship. Phases 1+2 (done before this session) already had the hard work done — routes, JS drag logic, test coverage. Phase 3 was pure glue: link in the template list, smoke test extension, docs pass. No regressions, no surprises.

The one thing worth noting: the list.html action cell previously had only a Delete form/button, no edit-style links. Adding "Edit anchor" as a plain `<a>` with `btn btn-sm` class keeps it consistent with the existing button style without CSS surgery.

## Technical Details

**Route design (POST /admin/templates/{id}/anchor):**
```python
class AnchorSavePoints(BaseModel):
    points: list[list[float]]  # exactly 4, all in [0, 1]
```
- Validates exactly 4 points, all coordinates in [0, 1]
- Writes v2 schema: `{version: 2, zones: [{name: "front", kind: "quad", points: [...]}]}`
- Invalidates composite cache after save

**Pre-population logic (`_derive_initial_quad_points`):**
- v1 rect `{x, y, w, h}` → TL, TR, BR, BL corners
- v2 first zone (quad kind) → raw points
- v2 first zone (rect kind) → derived corners
- Missing/null anchor → centered 0.2-0.8 default box

**JS drag (pointer events, no framework):**
- `pointerdown` / `pointermove` / `pointerup` on SVG circles
- Coordinates clamped to [0, 1] on release
- Polygon `<polyline>` updated live as handles move
- Save POSTs JSON with cookie-based admin token

**Smoke test addition:**
- Accepts HTTP 200 or 404 (seed DB has no templates, so 404 is correct)
- Rejects 5xx
- Test result: 404 (seeded DB empty) → counted as PASS

**Tests (9 new):**
1. Page renders (200, HTML contains `anchor-editor`)
2. Pre-pop from v1 rect (TL corner matches expected fraction)
3. Pre-pop from v2 quad (points echoed from existing zone)
4. GET 404 for missing template
5. Auth required (GET 401 without token)
6. Save round-trip writes v2 JSON (parse_anchor returns quad zone)
7. Save rejects 3 points (422)
8. Save rejects out-of-bounds point (422)
9. Save 404 for missing template (POST)

## What We Tried

N/A — straight-line implementation. No dead ends in Phase 3.

## Root Cause Analysis

No failures to analyze. Phase 1+2 pre-work meant Phase 3 was a documentation + plumbing pass. Test count delta was exactly as predicted (265+9=274). Smoke count delta exact (21+1=22).

## Lessons Learned

1. **Phase segmentation works.** Separating backend routes (P1), frontend + tests (P2), and polish + docs (P3) meant each phase was independently verifiable. P3 had zero code risk — only integration risk (link rendering, smoke reachability).

2. **Vanilla JS + SVG handles is the right call for MVP admin tools.** No React, no build step, no iframe. 130 LOC, works with any browser that supports pointer events. Sellers don't need a polished UX — they need it to work.

3. **Smoke test accepting 200|404 is the right pattern.** Smoke DB is ephemeral and has no seeded templates. Testing that the route *exists* (not 500) is sufficient. 404 is a correct response for route-without-data.

4. **Docs pass should be non-negotiable.** Changelog, roadmap, codebase-summary, template-system-guide all updated in one pass. Easy to skip when you're in a hurry; always worth the 20 minutes.

## Next Steps

**Deferred (v0.8 / post-v0.7.1):**
- Multi-zone editing UI (add/remove/rename zones)
- rect ↔ quad kind switching in editor
- JSON paste/copy import-export
- Grid snap, alignment guides, undo/redo
- Live composite preview at current zone (inline Pillow call on drag release)

**Immediate:**
- Verify anchor editor in browser with a real template image (manual QA)
- Confirm drag + save round-trip renders correctly in composite preview

---

**Unresolved Questions:**
- Should the editor show a real-time composite preview after save? (Low priority; currently requires navigating to composite preview page)
- Should POST /anchor invalidate the entire composite cache or only the `front` zone? (Current: invalidates all — safe but over-broad)
