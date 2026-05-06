# Notion Database Setup

Required for Phase 6 Notion review integration. Create the database manually in Notion, then share it with your integration.

## Step 1 — Create Integration

1. Go to https://www.notion.so/profile/integrations
2. Click **New integration** → name it `EtsyAuto`
3. Select your workspace → Submit
4. Copy the **Internal Integration Token** → set as `NOTION_API_KEY` in `.env`

## Step 2 — Create Database

Create a new **full-page database** in Notion (not inline). Add the following properties **exactly** (names are case-sensitive):

| Property Name | Type | Notes / Select Options |
|---|---|---|
| **Listing Title** | Title | Default title field — do not rename |
| **Etsy ID** | Number | Etsy listing ID (numeric) |
| **Status** | Select | Options: `new`, `processing`, `review`, `approved`, `pushed`, `failed` |
| **Selected Title** | Select | Options: `variant-1`, `variant-2`, `variant-3` |
| **Selected Mockup** | Select | Options: `variant-1`, `variant-2`, `variant-3` |
| **Listing URL** | URL | Full Etsy listing URL |
| **Synced At** | Date | ISO timestamp of last sync |
| **SQLite ID** | Number | Internal DB primary key (cross-reference) |

## Step 3 — Share with Integration

1. Open the database page in Notion
2. Click **...** (top-right) → **Connections** → **Connect to** → select `EtsyAuto`
3. Copy the database ID from the URL:
   `https://www.notion.so/{workspace}/{DATABASE_ID}?v=...`
4. Set `NOTION_DATABASE_ID=<DATABASE_ID>` in `.env`

## Step 4 — Environment Variables

Add to `backend/.env`:

```env
NOTION_API_KEY=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Cloudflare R2 (for public image hosting)
R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<r2_access_key>
R2_SECRET_ACCESS_KEY=<r2_secret_key>
R2_BUCKET_NAME=etsyauto-mockups
R2_PUBLIC_URL=https://pub-<hash>.r2.dev
```

## Step 5 — Cloudflare R2 Bucket

1. Log in to https://dash.cloudflare.com → **R2 Object Storage**
2. Create bucket named `etsyauto-mockups`
3. Enable **Public Access** on the bucket → note the public URL
4. Go to **Manage R2 API Tokens** → create token with **Object Read & Write** on the bucket
5. Copy Access Key ID and Secret Access Key → set in `.env`

## Review Workflow

Once configured, the backend will:

1. When a listing reaches `status=review` → `sync_to_notion` (runs every 30s) creates a Notion page with:
   - 3 title variant callout blocks (numbered)
   - 3 mockup image embeds (R2 public URLs)
2. User opens the Notion page, reviews options, then:
   - Sets **Selected Title** → `variant-1` / `variant-2` / `variant-3`
   - Sets **Selected Mockup** → `variant-1` / `variant-2` / `variant-3`
   - Sets **Status** → `Approved`
3. `pull_approvals` (runs every 60s) detects the `Approved` status + selections → updates SQLite: sets `listing.status=approved` and `selected=True` on chosen variants → triggers Phase 7 push

## Notes

- Both **Selected Title** and **Selected Mockup** must be set before approval is processed
- Setting Status=Approved without both selections → skipped until selections are filled
- SQLite is source-of-truth for all non-user fields; Notion is source-of-truth for user selections only
- R2 free tier: 10 GB storage + 10 M Class A ops/month (sufficient for MVP scale)
