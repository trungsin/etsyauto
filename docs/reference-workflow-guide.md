# Reference Mode Workflow Guide

EtsyAuto's Reference Mode lets you capture inspiration from public Etsy listings: scrape title + images, generate alternate titles via AI, remove the background from one image, and save the result to your Notion Idea Bank.

## Prerequisites

- Backend running on `http://localhost:8787` (or LAN IP)
- EtsyAuto Chrome extension v0.3.0+ loaded
- `.env` configured with `GEMINI_API_KEY`, `REMOVEBG_API_KEY`, `NOTION_API_KEY`, `NOTION_IDEA_BANK_DATA_SOURCE_ID` (see `notion-idea-bank-setup.md`)

## End-to-End Flow

### 1. Open a public Etsy listing

Navigate to any `https://www.etsy.com/listing/<id>` URL. The extension content script auto-detects the page and the side panel switches to **Reference Mode** automatically.

### 2. Scrape

Click **Scrape Listing** (or wait for auto-scrape on page load). The panel shows:
- Original title
- Up to 10 image thumbnails
- Listing URL + listing_id

Idempotency: scraping the same listing twice returns the existing reference row (no duplicate).

### 3. AI Suggest Title

Click **AI Suggest Title**. Backend calls Gemini 2.5 Flash with the original title and returns 3 variants (≤140 chars each) in under 5 seconds. Click any variant to copy it into the **Edited Title** field, or edit freely.

Calling Suggest a second time replaces the variants — does not append.

### 4. Remove Background

Click any thumbnail to select it, then click **Remove BG**. The backend pipes the image through remove.bg, uploads the cutout to R2, and creates a `Design` row with `source_type='reference_only'`. The cutout thumbnail appears beside the gallery.

> **Note:** Reference cutouts are excluded from the composite preview design dropdowns by design — they exist only for inspiration, not for product mockups.

Calling Remove BG again on a different image replaces the previous cutout (old R2 file deleted, FK updated).

### 5. Tag and Annotate

- **Tags:** click any of `style`, `color`, `layout`, `season`, `niche` to toggle. Multi-select.
- **Notes:** free-form text area.

### 6. Save to Idea Bank

Click **Save Reference**. The backend:
1. Creates (or updates) a Notion page in your Idea Bank database
2. Sets `Reference status = saved`
3. Embeds the cutout image as a Notion image block
4. Returns the Notion page URL — shown in the toast

Click the toast to open the new Notion page.

### 7. Manage Saved References

- **In the extension:** the side panel shows the saved status badge.
- **In Notion:** filter the Idea Bank by `Tags` or `Status` to find references later.
- **Backend admin:** `GET /references?tags=style&status=saved` (requires `X-Admin-Token`).

## Costs Per Reference

| Step | Provider | Approx Cost |
|------|----------|------------|
| Scrape | (DOM) | $0 |
| AI Suggest Title | Gemini 2.5 Flash | <$0.01 |
| Remove BG | remove.bg | ~$0.20 |
| Save to Notion | Notion API | $0 |
| **Total** | | **~$0.20** |

remove.bg's free tier is 50 cutouts/month — plenty for casual reference collection.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Side panel stuck on "Detecting…" | Reload the Etsy tab; check the URL matches `/listing/<id>` |
| Scrape returns empty title/images | Etsy DOM changed — check `chrome://extensions` console for selector errors, file an issue |
| Suggest Title returns 503 | Transient Gemini rate limit; retry once. If persistent, check `GEMINI_API_KEY` quota |
| Remove BG returns 503 | remove.bg quota exhausted or key invalid; check dashboard at remove.bg |
| Save returns 503 with `Notion Idea Bank not configured` | `NOTION_IDEA_BANK_DATA_SOURCE_ID` missing or wrong — see `notion-idea-bank-setup.md` |
| Cutout image not visible in Notion page | R2 bucket must be public; check `R2_PUBLIC_URL` resolves in a browser |

## Limits and Boundaries

- **One cutout per reference.** Re-running replaces it.
- **No bulk import.** One click = one reference (anti-bot friendly).
- **No reverse image search.** Tag manually.
- **Reference cutouts never enter composite preview.** They exist only as inspiration.

## Related Docs

- [Notion Idea Bank Setup](notion-idea-bank-setup.md) — prerequisite database setup
- [Notion DB Setup](notion-db-setup.md) — main review database (separate from Idea Bank)
- [System Architecture](system-architecture.md) — backend service flow
