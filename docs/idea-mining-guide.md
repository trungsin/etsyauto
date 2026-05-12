# Idea Mining Guide (v0.8)

How to find trending POD ideas on Etsy and turn them into draft listings — without ever leaving the admin UI.

---

## Overview

The v0.8 pipeline closes the gap between **what's selling** on Etsy and **what you're listing** in your shop:

1. **Add keywords** you want to track (e.g. `botanical print`, `moon phase wall art`).
2. The **scheduler** mines Etsy hourly, snapshotting the top listings for each keyword into the local SQLite DB.
3. Browse `/admin/ideas` — sorted by **velocity** (favorers gained per day) so the rising stars float to the top.
4. Click any idea → **3-step wizard** prefills title/tags/description into your existing template + design → creates an Etsy draft.

The whole loop is local — your shop, your DB, your decisions.

---

## How keywords work

A `keyword` is a search term plus an `enabled` flag. The miner runs only on `enabled=true` keywords on its hourly cadence.

| Field | Meaning |
|-------|---------|
| `term` | What you'd type into Etsy search |
| `enabled` | Pause/resume mining without deleting the keyword |
| `last_run_at` | When the miner last completed for this keyword (ops visibility) |

**Quota math.** Etsy's public API gives 10,000 calls/day. Each keyword costs ~26 calls/run (1 search + 25 listing-detail calls at the default `IDEA_MINER_PER_KEYWORD_LIMIT=25`). At hourly cadence: 26 × 24 = 624 calls/keyword/day. So **15 keywords ≈ 9,360 calls/day** — well under the cap. The admin UI warns above 20 active keywords.

---

## Add a keyword

1. Open `/admin/keywords`.
2. Type the term in the **Add keyword** box → submit.
3. The keyword starts mining on the next hourly tick. To trigger immediately, click **Fetch now** on the row (uses one search call + N detail calls right then).

**Pause mining for a keyword:** click the **Enabled** toggle. The row stays — you just stop spending API calls on it.

---

## Browse ideas

Open `/admin/ideas`. Default sort: **velocity** descending (highest favorers/day first).

### Filters

- **Source** — `etsy_api` (mined), `extension_passive` (logged when you visit a listing in-browser with the extension installed), `printful` / `printify` (v0.9+).
- **Status** — `new` (just mined), `saved` (you starred it), `drafted` (turned into a listing), `dismissed` (hidden).
- **Keyword** — narrow to one search term.

### What you see per row

- Reference image (primary image from Etsy)
- Title, current favorers, all-time views
- **Velocity** — `(favorers_now − favorers_first_seen) / days_observed`
- **Source link** — opens the Etsy listing in a new tab
- **Action** — "Create listing" → wizard

---

## The 3-step wizard

`/admin/ideas/{id}/create-listing` — stateless, all data carried via form fields. Refreshing in the middle is safe; nothing is committed until **Submit** on Step 3.

### Step 1 — Idea preview
- Shows the idea's title, tags, reference image, signal stats, blueprint fields (taxonomy, who_made, when_made).
- Pick which fields to **prefill** into the wizard via checkboxes.
- **IP banner** appears for `extension_passive` ideas or any idea with a `reference_image_url`. Read it. Reference images are inspiration only.

### Step 2 — Template + Design
- Pick from your existing templates (`/admin/templates` CRUD).
- Pick from your existing designs **or** upload a new RGBA PNG.
- Composite preview renders live (reuses v0.4 endpoint).
- **Reference-only designs are blocked** here — the listing creator rejects them server-side.

### Step 3 — Review + Submit
- Final form prefilled from idea + template defaults: title, tags, description, taxonomy_id, who_made/when_made, materials, price.
- Edit anything inline.
- Click **Create Etsy draft** → calls `listing_creator_service.create_from_template` (the same path `/listings/from-template` uses).
- On success: idea status flips to `drafted`, an `idea_to_listing` row records the link, and the success page shows the Etsy `listing_id` + draft URL.
- On failure: error template renders with the underlying message — no crash, no partial state.

---

## Extracting a design from the reference image (v0.8.3+)

Some sellers want to start a listing from the design printed on the reference image. The wizard now lets you crop a region of that image, strip the background, and use the cutout as the listing's design — auto-linked to the idea.

**How:**
1. Open `/admin/ideas/{id}/create-listing` (Step 1).
2. Click **Extract design from reference image →**.
3. Drag a rectangle over the design region in the modal; release to confirm.
4. Click **Extract & Save** — backend downloads the reference image, crops the region (PIL), removes background (remove.bg), uploads PNG to R2, and creates a Design with `source_type='derivative'`.
5. The Design is linked via `idea.design_id`. In Step 2 the radio is pre-selected automatically.
6. Continue the wizard as normal.

**Re-extracting** replaces the previous derivative for that idea (old R2 object is deleted; the same Design row is updated in place).

**Status badge** in Step 1 confirms a derivative is attached, with a thumbnail of the current cutout.

**Why `derivative` (not `reference_only`)?**
- `reference_only` designs are still blocked server-side (legacy inspiration cutouts).
- `derivative` is a new, eligible source_type — user-acknowledged that the artistic crop + background-strip is the seller's compliance decision, not the system's.

**IP & ToS:** You are responsible for compliance. Etsy may flag duplicate-image listings even when the mockup template differs. See the IP banner in Step 1.

---

## IP & ToS notes (read this)

The miner caches Etsy listing data **read-only** for sorting. It does not redistribute, repackage, or republish anything.

The reference image attached to each idea is for **your inspiration only**. Do not:
- Upload it as your design
- Trace it
- Generate near-identical mockups

Do:
- Use it as a vibe board
- Sketch fresh artwork
- Generate an Imagen variation with a substantially different prompt

The wizard's IP banner repeats this on every idea touched by an external image. If you ignore it and Etsy takedown-notices you, that's on you.

---

## Troubleshooting

### "No ideas appearing for my keyword"

1. Check `/admin/keywords` — is the keyword **enabled**?
2. Click **Fetch now** to trigger immediately — wait for `last_run_at` to update.
3. If still empty, the keyword may have zero matching public listings. Try a broader term.
4. Check server logs for `miner: keyword=<term> returned 0 summaries` — that confirms a real Etsy 0-result, not a client error.

### "Hit Etsy quota / 429 errors"

- Disable some keywords until the daily window resets (Etsy quotas reset on a 24h rolling window).
- Lower `IDEA_MINER_PER_KEYWORD_LIMIT` in `.env` (default 25 → try 15).
- The miner is **fail-closed per listing** — one bad call doesn't crash the run, it logs and continues.

### "Wizard submit failed"

- Read the error message on the error page; it's surfaced from `listing_creator_service`.
- Common causes:
  - Template has no size/color combinations configured → add variations under `/admin/templates`.
  - Title too long (>140 chars) — Etsy hard cap.
  - Design is `reference_only` — upload a fresh design.
- The **idea row stays `new`** on failure (status is only flipped on success), so you can retry.

### "I want Notion sync back"

Set `NOTION_SYNC_ENABLED=true` in `.env` and restart. The dormant `sync_to_notion` and `pull_approvals` jobs reactivate; no migration needed.

---

## What's next

- **v0.9** — Printful + Printify catalog API as additional `source` values; idea ↔ POD product matcher; bulk wizard mode.
- **v1.0** — TeePublic / Society6 / Zazzle passive scrape via extension.

See `docs/development-roadmap.md` for the full schedule.
