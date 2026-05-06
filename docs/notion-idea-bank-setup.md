# Notion Idea Bank Setup Guide

This guide walks through creating the Notion Idea Bank database and connecting it to EtsyAuto.

## Overview

The Idea Bank is a Notion database where EtsyAuto saves Etsy reference listings you want to keep for inspiration. Each page contains the listing title, URL, AI-suggested title variants, tags, notes, and optional cutout image.

## Prerequisites

- Notion account with EtsyAuto integration already connected (see `docs/notion-db-setup.md`)
- EtsyAuto backend running with `NOTION_API_KEY` set

---

## Step 1: Create a New Notion Database

1. Open Notion and navigate to your workspace.
2. Create a new **Full Page** (click `+` in the sidebar → select **Page**).
3. Give it a name, e.g. **"EtsyAuto Idea Bank"**.
4. Inside the page, type `/database` and select **Table — Full page**.

---

## Step 2: Configure Required Properties

Delete the default "Name" column and create the following properties **with exact names** (copy-paste to avoid typos):

| Property Name | Type | Notes |
|---|---|---|
| `Reference Title` | Title | Default title column — rename from "Name" |
| `Source URL` | URL | Etsy listing URL |
| `Original Title` | Text | Original scraped title |
| `Edited Title` | Text | Your edited version |
| `AI Variants` | Text | 3 AI-suggested titles separated by bullets |
| `Notes` | Text | Free-form notes |
| `Tags` | Multi-select | Add options: `style`, `color`, `layout`, `season`, `niche` |
| `Cutout Image` | Files & media | Stores R2 cutout PNG |
| `Status` | Select | Add options: `idea`, `used`, `archived` |
| `Created` | Date | Creation date |

**Important:** Property names are case-sensitive and must match exactly. EtsyAuto validates the schema on startup and logs warnings for mismatches.

---

## Step 3: Share with EtsyAuto Integration

1. Click **Share** (top-right) on the database page.
2. Under **Connections**, search for **EtsyAuto** (or the name you used when creating the integration).
3. Click **Invite** to grant the integration access.

---

## Step 4: Get the Data Source ID

1. Open the database as a full page (click the arrow icon top-right if in inline view).
2. Copy the URL — it looks like:
   ```
   https://www.notion.so/workspace/abc123def456...?v=xyz
   ```
3. The long hex string before `?v=` is the **database ID**.
4. To get the **data_source_id**, use the Notion API:
   ```bash
   curl -X GET https://api.notion.com/v1/databases/<database_id> \
     -H "Authorization: Bearer <NOTION_API_KEY>" \
     -H "Notion-Version: 2022-06-28"
   ```
   Look for `"data_source_id"` in the response — this is a different (shorter) identifier used by the data sources API.

   Alternatively, if the standard database_id works directly (older API style), you can use it as-is.

---

## Step 5: Set the Environment Variable

Add to your `.env` file:

```env
NOTION_IDEA_BANK_DATA_SOURCE_ID=<your_data_source_id_here>
```

Restart the backend:
```bash
pkill -f uvicorn; cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8787 &
```

---

## Step 6: Verify Schema

On startup, EtsyAuto logs:
```
INFO Notion Idea Bank schema validated OK
```

If properties are missing:
```
WARNING Notion Idea Bank schema mismatch — missing properties: Cutout Image, Tags
```

Fix the missing properties in Notion and restart.

---

## Step 7: Test the Integration

Save a reference to the Idea Bank:

```bash
curl -X POST http://localhost:8787/references/1/save \
  -H "X-Admin-Token: <your_admin_token>"
```

Expected response:
```json
{"notion_page_id": "abc-123-...", "status": "saved"}
```

The page appears immediately in your Notion Idea Bank database.

### Idempotency

Calling `/save` again on the same reference updates the existing Notion page (same `notion_page_id`) rather than creating a duplicate.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `503 Notion Idea Bank not configured` | Set `NOTION_IDEA_BANK_DATA_SOURCE_ID` in `.env` |
| `APIResponseError: object not found` | Integration not shared with the database — repeat Step 3 |
| Schema mismatch warnings on startup | Check property names match exactly (copy-paste from table above) |
| Cutout image not visible in Notion | R2 public URL must be publicly accessible; check `R2_PUBLIC_URL` in `.env` |
| Tags not saving | Add the tag options (`style`, `color`, `layout`, `season`, `niche`) to the Tags multi-select property |
