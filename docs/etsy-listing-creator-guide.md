# Etsy Listing Creator Guide

User guide for the EtsyAuto **Etsy Listing Creator** (Sub-feature C, v0.4.0).
Covers the end-to-end flow that turns a (template + design + colors + sizes) tuple into an Etsy *draft* listing with full variations matrix and per-color mockup images.

---

## Table of Contents

1. [What This Builds](#what-this-builds)
2. [Prerequisites](#prerequisites)
3. [Workflow Walkthrough](#workflow-walkthrough)
4. [API Reference](#api-reference)
5. [Cost Breakdown per Listing](#cost-breakdown-per-listing)
6. [Limits](#limits)
7. [Troubleshooting](#troubleshooting)

---

## What This Builds

For a typical 3-sizes × 5-colors apparel template with one design upload, the creator:

1. Renders **5 composite mockups** (one per color) via Pillow
2. Calls Etsy `POST /shops/{shop}/listings` → creates a **draft listing**
3. Calls Etsy `PUT /listings/{lid}/inventory` with **15 inventory rows** (size × color cartesian, minus any toggled-off combos)
4. Calls Etsy `POST /listings/{lid}/images` **5 times** (primary color first, rest by template order)
5. Persists `Listing(etsy_listing_id, template_id, design_id, status='created')` for idempotency

The listing stays in **draft** state on Etsy. Seller reviews, finalizes title/description/SEO, and publishes from the Etsy UI — the project never auto-publishes.

---

## Prerequisites

### Backend

- Templates with **per-color base images** uploaded (see `template-system-guide.md` → Multi-Color Base Images)
- `variation_options_json` containing `sizes[].price_cents`, `colors[]`, optional `primary_color`
- An uploaded `Design` with `source_type='upload'` (or `'ai_generated'`) — `reference_only` cutouts are **rejected** to enforce the IP boundary set in v0.3.0
- Etsy OAuth token in `api_credentials` (run `/auth/etsy/start` once)
- `ADMIN_TOKEN` env var

### Extension

- v0.4.0 extension installed
- Backend URL + admin token configured (gear icon in side panel)

---

## Workflow Walkthrough

### Step 1 — Open the Etsy create-listing page

Navigate to `https://www.etsy.com/your/shops/<your-shop>/listings/new`. The content script detects the `creator` URL pattern and switches the side panel into **Creator Mode**.

### Step 2 — Pick template + design

The side panel populates two dropdowns:
- **Template** — only those with at least one color in `variation_options.colors`
- **Design** — non-`reference_only` designs

Selecting a template auto-renders a **Variations Matrix** (rows = sizes, columns = colors) with all checkboxes enabled.

### Step 3 — Preview all color mockups

Click **Preview All Colors**. The extension calls `POST /composite/preview-all-colors`. Thumbnails appear per color (with a ⚡ marker for cache hits). Renders are parallel (5-thread pool); typical 3-color render <10s, 5-color <20s.

### Step 4 — Toggle off any combos

Uncheck cells in the Variations Matrix to skip combos (e.g. "no XS in Black"). The maximum allowed is **30 enabled combos** (Etsy hard cap).

### Step 5 — Fill listing meta

- **Shop ID** — your numeric Etsy shop_id (the URL slug is shown as a placeholder hint)
- **Title** — ≤140 chars (Etsy cap)
- **Description** — required, plain text (rich-text formatting is out of scope for v0.4.0)
- **Tags** — comma-separated, max 13 (the rest are dropped)

### Step 6 — Click "Create Etsy Draft"

The extension calls `POST /listings/from-template`. The backend:

1. Validates template + design + combos
2. **Idempotency check**: if a `Listing(template_id, design_id)` already exists, returns its `etsy_listing_id` immediately (no Etsy call)
3. Renders any missing composites
4. Resolves Etsy property `value_id`s for each (size, color) name (cached after first call)
5. Creates the draft, then updates inventory, then uploads images sequentially with a 200 ms gap (rate-limit safety)
6. Persists the local `Listing` row

A success toast contains a link: **"Open in Etsy"** → opens the draft edit page in a new tab.

### Step 7 — Finalize on Etsy

- Verify pricing per size, mockup ranks, tags, materials, processing time
- Adjust SEO category & attributes
- Click **Publish**

---

## API Reference

### `POST /listings/from-template`

```http
POST /listings/from-template
X-Admin-Token: <token>
Content-Type: application/json
```

```jsonc
{
  "template_id": 1,
  "design_id": 7,
  "title": "Custom Comfort Tee — Hand-Pressed",
  "description": "Soft 100% cotton ...",
  "tags": ["t-shirt", "custom", "gift"],
  "shop_id": "12345678",
  "enabled_combos": [
    {"size": "S", "color": "White", "enabled": true},
    {"size": "S", "color": "Black", "enabled": false},
    {"size": "M", "color": "White", "enabled": true}
  ],
  "quantity_per_variant": 100
}
```

**Response (201)**:

```jsonc
{
  "listing_id": 42,                    // local DB id
  "etsy_listing_id": "1234567890",
  "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/1234567890",
  "composite_urls": [
    {"color": "Sand", "url": "...", "rank": 1},
    {"color": "White", "url": "...", "rank": 2}
  ],
  "idempotent": false                  // true if returned from existing Listing row
}
```

**Errors**:

| Status | Cause |
|--------|-------|
| 400 | `reference_only` design, invalid combo size/color, no enabled combos |
| 404 | Template or design not found |
| 422 | More than 30 enabled combos, taxonomy resolve missing a value |
| 502 | Etsy API failed (logged, idempotent retry safe) |

---

## Cost Breakdown per Listing

| Item | Cost | Notes |
|------|------|-------|
| Composite renders (5 colors) | $0.00 | Pillow CPU-only; only counts R2 storage |
| R2 storage | ~$0.005 | 5 composites × ~1 MB each, perpetual |
| Etsy API calls (3–5 calls) | $0.00 | Within free quota; rate-limited per shop |
| **Total** | **~$0.01** | |

This compares to the v0.1.0 single-listing optimizer pipeline at ~$0.30–$0.50 (Claude + Imagen + remove.bg). The creator path skips AI mockups entirely — it composites the seller's own design.

---

## Limits

| Limit | Value | Source |
|-------|-------|--------|
| Inventory rows per listing | 30 | Etsy hard cap |
| Listing images | 10 | Etsy hard cap (we upload up to N colors, capped) |
| Tags | 13 | Etsy hard cap |
| Title length | 140 chars | Etsy hard cap |
| Image min dimension | 570×570 | Etsy recommendation; verify your composite output |
| Supported categories | apparel only | `taxonomy_id=1209`, properties: Color (200), Size (506) |

Other categories (mug, poster, sticker) require additional taxonomy IDs; track in the project roadmap.

---

## Troubleshooting

### "Etsy taxonomy ... has no value(s) matching ['Burgundy']"

Etsy's color enum is fixed per taxonomy. If your template's `variation_options.colors` includes a name Etsy doesn't recognize, the resolve step returns 422 with the closest matches. Solutions:

1. Pick a similar name from the available list (the error includes the first 20)
2. Set a custom `etsy_taxonomy_id` in `variation_options_json` if your category has a different palette

### "Idempotent return" — listing was already created, but I want a fresh one

Idempotency keys on `(template_id, design_id)`. To create a *new* draft from the same pair:
- Either change one input (e.g. duplicate the design upload)
- Or delete the existing local `Listing` row: `DELETE FROM listings WHERE template_id=X AND design_id=Y;` (manual SQL)

### "Composite preview is slow on first call (>30s)"

R2 cold cache + 5 colors composited in parallel can hit 20–30s on a low-CPU VM. Subsequent calls return instantly (cache hits, marked ⚡). The preview itself does not block listing creation — you can skip it and click Create Draft directly; composites are rendered on-demand server-side.

### Side panel doesn't auto-switch to Creator Mode

The content script matches `https://www.etsy.com/your/shops/*/listings/new` (and `/create-listing`). If Etsy A/B-tests a new URL pattern, file an issue — the regex needs updating in `content-scripts/listing-detector.js`.

### Etsy 429 rate limit during image upload

The creator already sleeps 200 ms between image uploads. If you still hit 429, the call retries once with backoff. Persistent failure: re-call `POST /listings/from-template` — idempotency returns the existing `etsy_listing_id` and resumes the image upload step (any image rank already uploaded on Etsy is preserved; the missing ones are re-attempted).

### "Failed to download template base image"

The composite step fetches `template.color_base_images_json[color]` via HTTP. If R2 public URL is misconfigured or the object is missing for that color, this 400s. Verify with:

```bash
curl -I "$(jq -r '.color_base_images_json | fromjson | .White' < template.json)"
# Expect 200
```

---

## Admin UI Alternative (v0.5.0)

For batch listing creation, multi-monitor workflows, or testing without leaving the Etsy seller dashboard, open `http://localhost:8787/admin/listings/creator` in any browser.

The page exposes the same flow as the Chrome extension Creator Mode — pick template, pick design, preview all colors, toggle combos in the variations matrix, fill listing meta, click **Create Etsy Draft**.

### Differences vs the Chrome extension

| Aspect | Extension | Admin UI |
|--------|-----------|----------|
| Activation | Auto on `/your/shops/*/listings/new` | Manual navigate |
| Shop ID | Auto-detect from URL slug | Manual entry (cached in `localStorage`) |
| Stack | Vanilla JS, MV3 service worker | Jinja2 + HTMX, server-rendered |
| Auth | Settings panel (gear icon) | Single prompt for `ADMIN_TOKEN`, cached in `localStorage` |
| Width | Side panel (~360 px) | Full browser width |

### Auth flow

On first visit the page prompts once for `ADMIN_TOKEN` and stores it in `localStorage` under key `admin_token`. Every HTMX request injects the value into `X-Admin-Token` via the `htmx:configRequest` listener. To rotate the token, run `localStorage.removeItem('admin_token')` in DevTools and reload.

### Routes added (v0.5.0)

| Method | Path | Returns |
|--------|------|---------|
| GET | `/admin/listings/creator` | Full page (Jinja) |
| GET | `/admin/listings/creator/template-info?template_id=X` | HTMX partial: variations matrix |
| POST | `/admin/listings/creator/preview` | HTMX partial: composite thumbnails grid |
| POST | `/admin/listings/creator/submit` | HTMX partial: success / idempotent / error toast |

All four routes are protected by `X-Admin-Token` and reuse the same backend services as the extension flow (no new business logic).

---

*Etsy Listing Creator — Sub-feature C of EtsyAuto v0.4.0 + admin UI v0.5.0*
*Related guides: `template-system-guide.md`, `reference-workflow-guide.md`*
