# Genmockup Product Mockup Pipeline Integration

**Date**: 2026-05-22 10:45
**Severity**: Medium
**Component**: Backend API, Admin Routes, Product Mockup Generation
**Status**: Resolved

## What Happened

Successfully integrated the genmockup product mockup generation pipeline (tshirt/poster/canvas/pillow mockups) into etsyauto's admin backend. The pipeline now sits alongside the existing anchor-rect template system at `/admin/product-mockup/` as a completely separate, non-conflicting feature.

Instead of wrestling with pip dependency conflicts (genmockup requires `opencv-python`, but etsyauto already uses `opencv-python-headless`), we embedded the 5 core pipeline files directly into the codebase as a local package at `app/genmockup_pipeline/` with fixed relative imports. Zero new external dependencies.

## The Brutal Truth

This should have been straightforward. It wasn't, because Python packaging is genuinely annoying when two packages provide the same module name (`cv2`). The initial instinct was "just pip install genmockup"—until uv caught the conflict. That one moment of "wait, both provide cv2?" saved us from a broken production build later.

The Starlette API discovery was the real kick in the teeth: the existing codebase uses `TemplateResponse(request, name, context)` (Starlette 1.0.0+), but genmockup's test code assumed the pre-1.0 API. TestClient caught it immediately, but we had to know what we were looking for. Small victories matter.

## Technical Details

### Dependency Conflict Root Cause
- `genmockup` → `opencv-python`
- `etsyauto` → `opencv-python-headless`
- Both export `cv2` into the same namespace
- `uv` refuses to resolve when both are in `pyproject.toml`

**Solution**: Extract genmockup's core pipeline logic (template processing, PIL-based seed generation, compositing) into `app/genmockup_pipeline/` as a local package. Fixed imports: `from genmockup_pipeline.pipeline import MockupGenerator` instead of `from genmockup.pipeline import ...`.

### Starlette 1.0.0 Breaking Change
Pre-1.0 template syntax:
```python
TemplateResponse("index.html", {"request": request, "data": ...})
```

Post-1.0 syntax (what etsyauto uses):
```python
TemplateResponse(request, "index.html", {"data": ...})
```

Genmockup's test code used old API. Fixed all 3 route handlers after TestClient failure.

### Seed Template Generation
Ran genmockup's PIL-only seed generation script (`scripts/generate_seeds.py`) locally with etsyauto's venv. Generated 12 base templates (3 color variants × 4 product types: tshirt, poster, canvas, pillow). Committed to `backend/static/templates_catalog/` as static assets.

### Code Review Fixes (Critical)
1. **Token validation**: Replaced local `_check_token()` helper with `Depends(require_admin_token)` dependency for consistency with existing routes
2. **Product validation**: Added explicit product type check → returns 422 if unknown product
3. **File handle leak**: Mask image was kept open outside `with` block. Fixed: use `.copy()` inside context manager, pass copy to compositing
4. **Error message sanitization**: Removed internal path leakage in error response (was exposing `templates_catalog/` directory structure)

## What We Tried

1. **Initial approach**: Install genmockup via pip
   - **Failed**: Dependency conflict. Both `opencv-python` and `opencv-python-headless` export `cv2`.
   
2. **Second approach**: Create Dockerfile with system-level opencv
   - **Rejected**: Overengineered. genmockup only needs PIL for seed generation (which works fine).

3. **Chosen approach**: Embed core pipeline as local package
   - **Succeeded**: 5 files, fixed imports, zero new deps. Lightweight and isolated.

## Root Cause Analysis

**Why the dependency conflict?**
- Python packaging doesn't force namespace uniqueness. Two packages can export the same top-level name.
- `opencv-python` = compiled wheels + C++ bindings
- `opencv-python-headless` = same, minus GUI dependencies (X11, etc.)
- Both satisfy `import cv2` but uv/pip can't have both in the same environment

**Why Starlette API mismatch?**
- Genmockup's codebase predates Starlette 1.0 release (2024)
- Test helpers hadn't been updated
- Lesson: always check framework versions when integrating external libraries

**Why the file handle leak went unnoticed in genmockup?**
- Genmockup's CLI usage is short-lived (single batch job per invocation)
- Handle wasn't closed until process exit
- In long-running etsyauto server, would accumulate over 100+ requests

## Lessons Learned

1. **Embedded packages beat dependency hell** — When two packages export the same name, embedding the logic you need as a local package is cleaner than fighting pip. Reduces surface area, improves debuggability.

2. **Framework version mismatches are silent killers** — TestClient caught the Starlette API issue immediately, but only because we tested. Pre-commit linting didn't flag it. Always test template routes, not just JSON endpoints.

3. **File handles in loops are subtle bugs** — The PIL image handle leak would have manifested as "too many open files" errors in production after 1000+ mockup requests. Context managers are your friend; don't get clever with `.copy()`.

4. **Static seed templates are the right move** — Generating at runtime adds latency and complexity. Pre-generating 12 variants and committing them lets the pipeline run with zero PIL calls—pure PIL-less PIL-dependent code.

## Next Steps

1. **Monitoring** — Add request metrics to `/admin/product-mockup/generate` endpoint. Watch for slow requests (> 500ms typically means file I/O bottleneck or missing cache).

2. **Template expansion** — Current 12 templates are minimal. Add more color variants and product types as demand grows. Process: run genmockup locally, commit to `templates_catalog/`.

3. **Cache layer** — If mockup generation becomes frequent, add Redis cache keyed by `(product_type, template_id, image_hash)` to avoid redundant PIL operations.

4. **Documentation** — Add brief README to `app/genmockup_pipeline/` explaining why it's embedded and how to update seeds if genmockup upstream changes.

5. **Security audit** — Product mockup endpoints now accept file uploads. Ensure image validation (size, format, malware scanning) before compositing. Currently not implemented.

---

**Files Created:**
- `app/genmockup_pipeline/__init__.py` (5 core files)
- `app/genmockup_pipeline/pipeline.py` (MockupGenerator, compositing logic)
- `app/genmockup_pipeline/template_handler.py` (template loading)
- `app/genmockup_pipeline/seed_generator.py` (PIL seed creation)
- `app/genmockup_pipeline/utils.py` (image transforms)
- `app/services/product_mockup_service.py` (high-level API)
- `app/routes/product_mockup_admin.py` (3 endpoints: GET catalog, POST generate)
- `app/templates/mockup/index.html` (admin UI)
- `backend/static/templates_catalog/` (12 pre-generated seed templates)

**Files Modified:**
- `config.py` (+`genmockup_template_root` setting)
- `main.py` (+product_mockup_admin router)
- `templates/base.html` (+navigation link to mockup admin)

**Tests All Pass:**
- GET `/admin/product-mockup/` → 200, renders form
- GET `/admin/product-mockup/catalog` → 200, lists 4 product types
- POST `/admin/product-mockup/generate` (tshirt) → 200, 3 static URLs
- POST `/admin/product-mockup/generate` (poster) → 200, 3 static URLs
- POST `/admin/product-mockup/generate` (unknown product) → 422 validation error
- Missing admin token → 401

**Unresolved Questions:**
- Should we pre-generate more template variants (10+ colors per product)?
- Image upload validation—what file size and format limits?
- Should cache layer use Redis or in-memory (gunicorn workers)?
