# Template System Guide

User guide for the EtsyAuto **Template System & Mockup Composer** (Sub-feature B).
Covers setup, admin UI walkthrough, API reference, composite anchor conventions, troubleshooting, and the manual QA checklist.

---

## Table of Contents

1. [Overview](#overview)
2. [Setup — Required Environment Variables](#setup--required-environment-variables)
3. [Admin UI Walkthrough](#admin-ui-walkthrough)
4. [API Reference](#api-reference)
5. [Composite Anchor Convention](#composite-anchor-convention)
6. [Troubleshooting](#troubleshooting)
7. [Manual UI Checklist](#manual-ui-checklist)

---

## Overview

The Template System allows you to upload product blank images (t-shirts, mugs, posters, etc.), define where a design should be placed (the "anchor"), and generate composite preview images by pasting your design onto the template. This is the foundation for automated Etsy listing creation with POD (Print on Demand) variations.

**Flow:**

```
Upload Template (blank product image)
         │
         ▼
Define Composite Anchor (where design goes on the blank)
         │
         ▼
Add Variations Matrix (sizes × colors, up to 30 rows)
         │
         ▼
Upload Design (transparent PNG artwork)
         │
         ▼
POST /composite/preview → Pillow alpha-pastes design onto template
         │
         ▼
Composite PNG cached in Cloudflare R2
```

---

## Setup — Required Environment Variables

All variables are set in `backend/.env`. See `deployment-guide.md` for full setup.

### Template System Specific

| Variable | Required | Example | Purpose |
|----------|----------|---------|---------|
| `ADMIN_TOKEN` | Yes | `my-secret-token-abc` | Authenticates all `/templates`, `/designs`, `/composite`, and `/admin/*` endpoints |
| `R2_ACCOUNT_ID` | Yes | `abc123def456` | Cloudflare R2 account ID |
| `R2_ACCESS_KEY_ID` | Yes | `key_abc123` | R2 API token key ID |
| `R2_SECRET_ACCESS_KEY` | Yes | `secret_xyz789` | R2 API token secret |
| `R2_BUCKET_NAME` | Yes | `etsyauto-assets` | R2 bucket for templates, designs, and composite previews |
| `R2_PUBLIC_URL` | Yes | `https://pub-abc123.r2.dev` | Public base URL for your R2 bucket |

### R2 Bucket Structure

The template system writes to these R2 prefixes:

```
{R2_BUCKET_NAME}/
├── templates/        ← base blank images (uploaded via POST /templates)
├── designs/          ← design artwork PNGs (uploaded via POST /designs)
└── composites/       ← generated previews, key = {template_id}-{design_id}.png
```

Composite previews are cached — identical `(template_id, design_id)` pairs return the cached R2 URL until the template or design is updated/deleted.

---

## Admin UI Walkthrough

Access the admin UI at `http://localhost:8787/admin/templates` after starting the backend.
All pages require the `X-Admin-Token` header — the browser sends this automatically if you set a cookie or use the URL with the token in the request (the Jinja2 UI reads it from a session cookie set on first auth).

> Note: The admin UI is server-rendered Jinja2 + HTMX. No JavaScript framework. Actions submit forms and the page re-renders inline via HTMX swaps.

### Page: Template List (`/admin/templates`)

Displays all templates in a table with columns: ID, name, category, price, # variations, created date, actions (Edit, Delete).

- Click **"New Template"** button to open the upload form.
- Click **"Edit"** on any row to open the template detail/edit page.
- Click **"Delete"** to remove the template and all its variations (cascade). R2 image and any cached composites are also deleted.

### Page: New Template Form

Fields:

| Field | Type | Notes |
|-------|------|-------|
| Name | Text | e.g. "Classic Unisex T-Shirt" |
| Category | Select | apparel, drinkware, print, accessories, other |
| Base Image | File upload | PNG or JPEG, ≤20MB. The blank product mockup. |
| Composite Anchor | JSON | `{"x": 0.2, "y": 0.3, "w": 0.6, "h": 0.4}` — see Anchor Convention below |
| Default Price (cents) | Integer | e.g. `2500` = $25.00 |
| Variation Options | JSON | `{"sizes": ["S","M","L"], "colors": ["white","black"]}` — metadata only |

After submit, the image uploads to R2 and the template record is created. You land on the template detail page.

### Page: Template Detail / Edit

Shows the template image, anchor overlay diagram, and the variations matrix. You can:

- **Edit metadata** — change name, price, anchor, category via an inline form. Saving a template update invalidates all composite previews for that template in R2.
- **Manage Variations** — the matrix shows all size/color combinations. Use **"Replace All Variations"** to submit a complete new matrix (old rows are atomically replaced). Maximum 30 rows enforced.

### Page: Design Library (`/admin/templates` → Designs section)

Accessible as a tab or separate section. Lists all uploaded designs with source type, dimensions, and upload date.

- Click **"Upload Design"** to open the design upload form.
- Source types: `upload` (user-provided PNG), `ai_generated` (future), `reference_only` (extension cutout — excluded from composite).
- Only `upload` and `ai_generated` source types can be composited.

### Page: Composite Preview

From the template detail page, select a design from the dropdown and click **"Generate Preview"**. The page calls `POST /composite/preview` via HTMX and displays the result inline.

- First call generates the composite and caches it in R2 (shows "Generated fresh").
- Subsequent calls with the same template + design return the cached URL instantly (shows "Cached").
- After editing the template or deleting the design, the cache is automatically invalidated.

---

## API Reference

All endpoints require `X-Admin-Token: {your_token}` header.
Base URL: `http://localhost:8787`

### Templates

| Method | Path | Body / Params | Status | Response |
|--------|------|---------------|--------|----------|
| `GET` | `/templates` | — | 200 | `[{id, name, category, base_image_url, composite_anchor, default_price_cents, variation_options, created_at}]` |
| `POST` | `/templates` | multipart: `name`, `category`, `composite_anchor` (JSON str), `default_price_cents`, `variation_options` (JSON str), `base_image` (file) | 201 | Template object |
| `GET` | `/templates/{id}` | — | 200 / 404 | Template object |
| `PUT` | `/templates/{id}` | JSON: any subset of `{name, category, composite_anchor, default_price_cents, variation_options}` | 200 | Updated template; invalidates composite cache |
| `DELETE` | `/templates/{id}` | — | 204 / 404 | Deletes template + variations + R2 images + composite cache |

**POST /templates example:**

```bash
curl -X POST http://localhost:8787/templates \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "name=Classic T-Shirt" \
  -F "category=apparel" \
  -F 'composite_anchor={"x":0.2,"y":0.25,"w":0.6,"h":0.5}' \
  -F "default_price_cents=2500" \
  -F 'variation_options={"sizes":["S","M","L"],"colors":["white","black"]}' \
  -F "base_image=@/path/to/tshirt-blank.png"
```

**PUT /templates/{id} example:**

```bash
curl -X PUT http://localhost:8787/templates/1 \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"default_price_cents": 2800}'
```

---

### Variations

| Method | Path | Body | Status | Response |
|--------|------|------|--------|----------|
| `GET` | `/templates/{id}/variations` | — | 200 / 404 | `{template_id, variations: [{id, size, color, price_cents, sku}]}` |
| `POST` | `/templates/{id}/variations` | JSON: `{variations: [{size, color, price_cents, sku?}]}` | 200 / 400 / 404 / 409 | `{template_id, variations: [...]}` — atomically replaces all |
| `PUT` | `/templates/{id}/variations/{vid}` | JSON: `{price_cents?, sku?}` | 200 / 404 | Updated variation |
| `DELETE` | `/templates/{id}/variations` | — | 204 | Clears all variations for template |

**Constraints:**
- Maximum 30 variations per template (Etsy hard limit)
- `(size, color)` must be unique within a template — duplicate returns 409

**POST /templates/{id}/variations example (6 rows, 3×2):**

```bash
curl -X POST http://localhost:8787/templates/1/variations \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "variations": [
      {"size": "S",  "color": "white", "price_cents": 2500, "sku": "TS-S-WHT"},
      {"size": "S",  "color": "black", "price_cents": 2500, "sku": "TS-S-BLK"},
      {"size": "M",  "color": "white", "price_cents": 2600, "sku": "TS-M-WHT"},
      {"size": "M",  "color": "black", "price_cents": 2600, "sku": "TS-M-BLK"},
      {"size": "L",  "color": "white", "price_cents": 2700, "sku": "TS-L-WHT"},
      {"size": "L",  "color": "black", "price_cents": 2700, "sku": "TS-L-BLK"}
    ]
  }'
```

---

### Designs

| Method | Path | Body / Params | Status | Response |
|--------|------|---------------|--------|----------|
| `GET` | `/designs` | `?source_type=upload\|ai_generated\|reference_only&limit=50&offset=0` | 200 | `{designs: [...], total, limit, offset}` |
| `POST` | `/designs` | multipart: `name`, `source_type`, `file` (PNG with alpha) | 201 / 400 | Design object |
| `GET` | `/designs/{id}` | — | 200 / 404 | Design object |
| `DELETE` | `/designs/{id}` | — | 204 / 404 | Deletes design + R2 file + composite cache entries |

**Design constraints:**
- File must be PNG with alpha channel (RGBA) — JPEG or RGB-only PNG returns 400
- Max file size: 10 MB
- `source_type=reference_only` designs cannot be composited (returns 400 on composite attempt)

**POST /designs example:**

```bash
curl -X POST http://localhost:8787/designs \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "name=Star Logo" \
  -F "source_type=upload" \
  -F "file=@/path/to/star-logo.png"
```

---

### Composite Preview

| Method | Path | Body | Status | Response |
|--------|------|------|--------|----------|
| `POST` | `/composite/preview` | JSON: `{template_id, design_id}` | 200 / 400 | `{composite_url, template_id, design_id, cached}` |

- `cached: false` — composite was generated fresh and uploaded to R2
- `cached: true` — composite already existed in R2 for this `(template_id, design_id)` pair
- Cache key: `composites/{template_id}-{design_id}.png`
- Cache is invalidated (R2 key deleted) when template or design is updated/deleted

**POST /composite/preview example:**

```bash
curl -X POST http://localhost:8787/composite/preview \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"template_id": 1, "design_id": 1}'
# → {"composite_url": "https://pub-abc.r2.dev/composites/1-1.png", "cached": false}
```

---

### Admin UI Endpoints (HTML)

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/admin/templates` | Template list page (Jinja2 HTML) |
| `GET` | `/admin/templates/new` | New template form |
| `GET` | `/admin/templates/{id}` | Template detail + variations matrix |
| `GET` | `/admin/templates/{id}/edit` | Edit template form |
| `POST` | `/admin/templates/{id}/delete` | Delete with HTMX confirmation |
| `GET` | `/admin/templates/{id}/composite` | Composite preview page |

---

## Composite Anchor Convention

The anchor defines the rectangular region of the template where the design is pasted.
All values are **fractions of the template image dimensions** (0.0 to 1.0).

```
┌─────────────────────────────────────┐
│           Template Image            │
│  (0,0)                    (1,0)     │
│   ┌───────────────────────────────┐ │
│   │                               │ │
│   │   x=0.2, y=0.25              │ │
│   │   ┌─────────────────┐        │ │
│   │   │                 │        │ │
│   │   │   Design goes   │ h=0.5  │ │
│   │   │     here        │        │ │
│   │   │                 │        │ │
│   │   └─────────────────┘        │ │
│   │        w=0.6                 │ │
│   └───────────────────────────────┘ │
│  (0,1)                    (1,1)     │
└─────────────────────────────────────┘

Anchor: {"x": 0.2, "y": 0.25, "w": 0.6, "h": 0.5}

  x — left edge of design region, as fraction of template width
  y — top edge of design region, as fraction of template height
  w — width of design region, as fraction of template width
  h — height of design region, as fraction of template height

Pixel coordinates (computed internally):
  left   = x * template_width
  top    = y * template_height
  right  = (x + w) * template_width
  bottom = (y + h) * template_height
```

**Design is scaled** (maintaining aspect ratio) to fill the anchor region using Pillow `Image.LANCZOS` resampling, then alpha-composited onto the template.

**Typical anchor values by product type:**

| Product | Typical Anchor |
|---------|----------------|
| T-shirt (front chest) | `{"x": 0.30, "y": 0.22, "w": 0.40, "h": 0.38}` |
| Mug (center wrap) | `{"x": 0.15, "y": 0.25, "w": 0.70, "h": 0.50}` |
| Poster (full bleed, margin) | `{"x": 0.05, "y": 0.05, "w": 0.90, "h": 0.90}` |
| Phone case (back panel) | `{"x": 0.10, "y": 0.15, "w": 0.80, "h": 0.65}` |

Values outside `[0, 1]` are clamped at the image boundary.

---

## Troubleshooting

### 1. `401 Unauthorized` on all endpoints

**Symptom:** Every API call returns `{"detail": "Unauthorized"}`.

**Cause:** `ADMIN_TOKEN` env var not set, or the header name is wrong.

**Fix:**
- Verify `backend/.env` contains `ADMIN_TOKEN=your-token-here`
- Restart the backend after editing `.env`
- Confirm the request header is `X-Admin-Token`, not `Authorization` or `Admin-Token`
- Test: `curl -H "X-Admin-Token: your-token" http://localhost:8787/templates`

---

### 2. `400 Bad Request: File must be a PNG with an alpha channel`

**Symptom:** Design upload fails with alpha channel error.

**Cause:** The uploaded PNG is in RGB mode (no transparency layer), or the file is JPEG.

**Fix:**
- Open the image in Photoshop/GIMP and verify the mode is RGBA (Image → Mode → RGB Color + layer with transparency)
- Export as PNG — not "PNG-8" (indexed) or JPEG
- Quick check in Python: `from PIL import Image; img = Image.open("file.png"); print(img.mode)` — must print `RGBA`

---

### 3. Composite preview is blank / design not visible

**Symptom:** `POST /composite/preview` returns 200 with a URL, but the composite image shows only the blank template with no design.

**Causes and fixes:**
- **Anchor region too small:** `w` or `h` close to 0. Check your anchor values — minimum useful size is ~0.1.
- **Design alpha is zero everywhere:** The PNG file may have a fully transparent alpha channel. Re-export with visible content.
- **Wrong anchor position:** The anchor `x + w > 1.0` or `y + h > 1.0` shifts the region out of bounds — it gets clamped to the image edge. Recalculate anchor to keep the region within bounds.

---

### 4. `R2 upload failed` or `ConnectionError` on composite preview

**Symptom:** `POST /composite/preview` returns 500 or 502 with an R2-related error.

**Causes:**
- R2 credentials missing or incorrect in `.env`
- Bucket name wrong or bucket not created in Cloudflare dashboard
- `R2_PUBLIC_URL` missing trailing slash or pointing to wrong bucket

**Fix:**
- Check `backend/.env` for all 5 R2 vars: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_URL`
- Verify the R2 token has `Object Read & Write` permissions on the target bucket
- Test connectivity: `uv run python -c "from app.clients.r2_storage_client import R2StorageClient; r2 = R2StorageClient(); r2.upload_image(b'test', 'smoke/test.txt'); print('OK')"`

---

### 5. Variations POST returns `409 Conflict`

**Symptom:** `POST /templates/{id}/variations` returns `{"detail": "Duplicate (size, color) pair: ..."}`.

**Cause:** The submitted variations list contains two or more rows with the same `(size, color)` combination.

**Fix:**
- Review your variations list for duplicate rows — Etsy treats each `(size, color)` as a unique SKU, so duplicates are not allowed.
- If you intended different prices for the same size/color, consider renaming sizes (e.g., "S-premium", "S-standard") or use SKU field to differentiate, but keep `(size, color)` unique.
- Note: `POST /templates/{id}/variations` does a full atomic **replace** — it is safe to call multiple times. Previously saved variations are deleted before inserting the new batch.

---

## Manual UI Checklist

Use this checklist to verify the admin UI is working end-to-end after deployment or after a code change.

### Setup Verification

- [ ] Backend starts without errors: `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload`
- [ ] `/health` returns 200: `curl http://localhost:8787/health`
- [ ] `ADMIN_TOKEN` is set in `backend/.env`

### Template CRUD

- [ ] **Browse** `/admin/templates` — page loads, shows "No templates yet" if empty
- [ ] **Create** template via form — upload a PNG blank, fill all fields, submit → row appears in list
- [ ] **View** template detail page — base image displayed, anchor values shown, variations table visible
- [ ] **Edit** template metadata — change default price, save → price updates in list view
- [ ] **Verify** that editing a template does not break existing template list

### Variations Matrix

- [ ] **Add variations** — enter 6 rows (3 sizes × 2 colors: S/M/L × white/black), submit → all 6 appear in table
- [ ] **Replace variations** — submit a new batch of 3 different rows → old 6 replaced by 3
- [ ] **Duplicate rejection** — submit two rows with identical size+color → form shows error, no partial save
- [ ] **Over-limit rejection** — attempt 31 rows → form shows "maximum 30 variations" error
- [ ] **Clear all** — delete button for all variations → table shows empty state

### Design Upload

- [ ] **Upload design** — select a PNG file with transparency (RGBA), give it a name, submit → appears in design library
- [ ] **JPEG rejection** — attempt to upload a .jpg file → clear error message about PNG required
- [ ] **RGB rejection** — attempt to upload a PNG without alpha channel → error about alpha required
- [ ] **Oversized rejection** — attempt to upload a file >10MB → error about size limit

### Composite Preview

- [ ] **Generate preview** — select a template and a design, click preview button → composite image appears
- [ ] **Verify design placement** — the design is visible on the template at the anchor region
- [ ] **Cache indicator** — first preview shows "Generated fresh" (or `cached: false` in API)
- [ ] **Second preview** — same template + design shows "Cached" (`cached: true`)
- [ ] **Cache invalidation** — edit the template (change price) → generate preview again → shows fresh (not cached)
- [ ] **reference_only rejection** — upload a design with `source_type=reference_only`, attempt composite → error returned

### Cleanup

- [ ] **Delete design** — deletes from library, R2 file removed, composite cache for that design cleared
- [ ] **Delete template** — cascades: all variations deleted, R2 image removed, composite cache cleared
- [ ] **Verify R2 cleanup** — after deleting template, check R2 bucket that `templates/` and `composites/` keys are gone

---

## Multi-Color Base Images (v0.4.0)

For Comfort-Colors–style listings where each colorway has a distinct mockup blank, you upload one base image per color. Composite preview is then rendered against the *correct* color's blank — not a single hardcoded one.

### Why per-color matters

A heather grey blank composited with a red logo looks fine. The same red logo on a yellow blank composited as if it were grey ships the wrong colors to your customer. Per-color bases keep mockup truth = product truth.

### Convention in `variation_options_json`

```jsonc
{
  "sizes": [
    {"name": "S",  "price_cents": 1900},
    {"name": "M",  "price_cents": 1900},
    {"name": "XL", "price_cents": 2200}
  ],
  "colors": ["White", "Black", "Sand", "Forest"],
  "primary_color": "Sand",        // optional — used as Etsy listing primary image (rank=1)
  "etsy_taxonomy_id": 1209          // optional — overrides default apparel taxonomy
}
```

### API: per-color base image upload

| Method | Path | Body | Status | Response |
|--------|------|------|--------|----------|
| `POST` | `/templates/{id}/color-bases/{color}` | multipart: `base_image` (PNG) | 200 / 400 / 404 / 413 | Updated template |
| `DELETE` | `/templates/{id}/color-bases/{color}` | — | 204 / 404 | Removes that color's base |

`{color}` must be present in `variation_options.colors`. Re-uploading replaces the existing R2 object (best-effort delete) and keeps the URL stable in `color_base_images_json`.

```bash
curl -X POST http://localhost:8787/templates/1/color-bases/Sand \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "base_image=@blanks/comfort-sand.png"
```

### API: cartesian variations + multi-color preview

```bash
# 1. Auto-build variations matrix from variation_options (sizes × colors)
curl -X POST http://localhost:8787/templates/1/expand-variations \
  -H "X-Admin-Token: $ADMIN_TOKEN"
# → 12 rows for 3 sizes × 4 colors

# 2. Render composites for every color in parallel
curl -X POST http://localhost:8787/composite/preview-all-colors \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"template_id": 1, "design_id": 1}'
# → {results: [{color: "White", composite_url, cached}, ...]}
```

Cache key includes color: `composites/{template_id}-{design_id}-{Color}.png`. A template price update invalidates *all* color variants.

### Image rank ordering

When the listing creator uploads composites to Etsy, ranks are assigned:
- `rank=1` → `variation_options.primary_color` (becomes Etsy thumbnail)
- `rank=2..N` → remaining colors in the order listed in `variation_options.colors`

---

## Anchor Schema v2 — Zones (v0.6.0)

v0.6.0 extends `composite_anchor_json` from a single rectangle to an array of typed **zones**. Existing v0.4.x templates auto-upgrade in memory at read time; the on-disk JSON stays untouched until next admin write.

### Schema reference

```jsonc
{
  "version": 2,
  "zones": [
    {
      "name": "front",
      "kind": "quad",
      "points": [
        [0.20, 0.20],   // top-left
        [0.80, 0.22],   // top-right
        [0.78, 0.78],   // bottom-right
        [0.22, 0.76]    // bottom-left
      ]
    },
    {
      "name": "back",
      "kind": "rect",
      "x": 0.30, "y": 0.30, "w": 0.40, "h": 0.30
    }
  ]
}
```

### Zone kinds

| Kind | Engine | Use case |
|------|--------|----------|
| `rect` | Pillow alpha-paste (v1 path, byte-identical to legacy) | Flat surfaces — print-on-flat tee, paper, sticker |
| `quad` | `cv2.warpPerspective` with INTER_LANCZOS4 | Tilted shots, mug curve, hat brim, perspective angles |

### Conventions

- **Coordinates:** all 0-1 fractions of the base image. `[0,0]` = top-left.
- **Quad order:** clockwise from top-left (TL, TR, BR, BL).
- **Layering:** zones render in array order — earlier = bottom, later = top.
- **Max zones:** 4 per template (soft cap, increase if needed).
- **Zone names:** alphanumeric; used as the key in `zone_designs` and the cache filename.

### Backward compat

v1 templates `{x, y, w, h}` continue to render unchanged via the `parse_anchor` shim. The shim wraps them into a single zone named `main`. If you POST a v1 anchor to `POST /templates`, the row is stored as v1 — only newly written templates use v2.

### Multi-design per template

When `POST /listings/from-template` is called with `zone_designs: {zone_name: design_id}`, each zone uses its mapped design. Zones omitted from the map fall back to the body's `design_id` field.

```bash
curl -X POST http://localhost:8787/listings/from-template \
  -H "X-Admin-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "design_id": 5,
    "title": "Front + Back Tee",
    "description": "...",
    "shop_id": "12345678",
    "enabled_combos": [{"size":"M","color":"White"}],
    "zone_designs": {"front": 5, "back": 7}
  }'
```

### Cache key format

| Scenario | Key |
|----------|-----|
| Single-design (legacy) | `composites/{tid}-{did}.png` |
| Single-design + color | `composites/{tid}-{did}-{Color}.png` |
| Multi-zone distinct designs | `composites/{tid}-{hash10}-{Color}-multi.png` |

`hash10` is the first 10 chars of SHA-1 of `front-5_back-7` (sorted zone-design pairs).

### What's not in v0.6.0

- Auto-anchor detection (deferred to C2)
- PSD smart-object pipeline (deferred to C3)
- Fabric displacement maps (deferred to C4)

---

## Image Pool (v0.9+)

A template can host up to 20 images, each with its own anchor zones (≤2 per image). The image pool is the foundation for per-template image rotation: listing creator renders all images, sorts by rank, uploads top 10 to Etsy, and optionally binds per-color images as Etsy variation hero images.

### Image roles

| Role | Compositing | Use case |
|------|-------------|----------|
| `mockup` (default) | Design is composited onto image via anchor zones | Product blank with design |
| `lifestyle_no_fill` | Image uploaded as-is, no compositing | Size chart, model photo, scene context |

### Image fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | PK, auto-increment |
| `image_url` | string (500) | R2 URL; required |
| `color` | string (50) | Optional; title-cased. Null means universal (no color tag) |
| `rank` | integer | Sort order (0, 1, 2, ...); unique per template |
| `anchor_json` | string (400) | JSON anchor schema (v1 or v2); default `"{}"` |
| `role` | string (20) | `"mockup"` or `"lifestyle_no_fill"` |
| `created_at` | datetime | Server-generated |

### Admin REST API — Image Pool

Base path: `/admin/templates/{tid}/images`
All endpoints require `X-Admin-Token` header.

| Method | Path | Body / Form | Status | Response |
|--------|------|-------------|--------|----------|
| `POST` | `""` | multipart: `file`, `color?`, `rank=0`, `role='mockup'`, `anchor_json='{}'` | 201 | Image object |
| `GET` | `""` | — | 200 | `[{id, image_url, color, rank, role, anchor_json, is_virtual}]` |
| `PATCH` | `"/{img_id}"` | JSON: `{image_url?, color?, rank?, role?, anchor_json?}` | 200 | Updated image |
| `DELETE` | `"/{img_id}"` | — | 204 | — |
| `POST` | `"/reorder"` | JSON: `[{id, rank}, ...]` | 200 | `{reordered: count}` |
| `POST` | `"/migrate"` | — | 200 | `{materialized: count}` |

**POST upload example:**
```bash
curl -X POST http://localhost:8787/admin/templates/1/images \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "file=@my-image.png" \
  -F "color=White" \
  -F "rank=1" \
  -F "role=mockup"
```

### Backward compatibility — virtualization

Templates created before v0.9 (no `template_images` rows) continue to work via **on-the-fly virtualization**:
- When listing images for a template with no real rows, `list_for_template()` synthesizes rows from `Template.base_image_url` + `color_base_images_json`.
- Virtual rows are read-only and flagged `is_virtual=true`.
- **First admin edit auto-materializes:** When you POST, PATCH, or DELETE an image, the service automatically materializes all virtual rows into the `template_images` table (1-time migration).
- Use the **"Migrate from legacy"** button (POST `/images/migrate`) to materialize without editing.

### Constraints

- **Max 20 images per template** (enforced on create)
- **Max 2 zones per image** (enforced via `anchor_schema.validate_for_image`)
- **Unique rank per template** (DB constraint)
- **Color must exist in `variation_options.colors`** (enforced on create/update)
- **Max 10 images uploaded to Etsy** (listing creator clips top 10 by rank)

### Etsy variation hero images (per-color binding)

When a template image has `color` set and the listing creator is called:
1. After rendering the image, the listing creator looks up the corresponding Etsy color variant.
2. If a match exists, the image is bound to that variant via `set_variation_images` Etsy API.
3. The image appears on the Etsy listing page when the customer selects that color.

If the color doesn't match any variant in the listing's `enabled_combos`, the image is uploaded to the gallery (rank-ordered) but not bound as a variation hero.

### Admin UI — Template Detail Page (`/admin/templates/{id}`)

Below the variations matrix, an **Image Pool** section displays:
- **Drag-drop upload zone** — upload PNG/JPEG; form fields for color, rank, role, anchor
- **Sortable table** — thumbnail preview, color tag, rank, role, edit anchor (modal), delete
- **"Migrate from legacy"** button — materializes virtual rows if template is pre-v0.9

Anchor editor opens in a modal; save invalidates composites for that image.

---

### Visual Anchor Editor (v0.7.1)

For non-dev sellers, edit a template's quad zone visually:

1. Open `/admin/templates` → click "Edit anchor" on a row
2. The page renders the base image with 4 draggable corner handles
3. Drag any corner; the polygon outline + saved coords update live
4. Click **Save** — backend writes v2 schema with single `quad` zone

Pre-population:
- v1 rect → corners (TL, TR, BR, BL)
- v2 first zone → its 4 corners (rect auto-converted to quad on first edit)
- Empty/new template → centered 0.2-0.8 box

Limits: MVP supports 1 zone only. Multi-zone editing requires editing
`composite_anchor_json` directly (or wait for v0.8 multi-zone editor).

---

*Template System — Sub-feature B (v0.2.0) + Multi-Color (v0.4.0) + Quad Zones (v0.6.0) of EtsyAuto*
*Related plans: extension-reference-upgrade (Sub-feature A), etsy-listing-creator (Sub-feature C)*
*See `etsy-listing-creator-guide.md` for the end-to-end creator workflow.*
