"""Notion API client — wraps notion-client SDK for review page management."""
import logging
from datetime import datetime, timezone
from typing import Any

from notion_client import Client

from app.config import settings

logger = logging.getLogger(__name__)

_STATUS_OPTIONS = ["new", "processing", "review", "approved", "pushed", "failed"]


class NotionClient:
    """Thin wrapper around the official notion-client SDK.

    All methods raise on unexpected API errors. Callers should handle
    notion_client.errors.APIResponseError for structured error handling.
    """

    def __init__(self) -> None:
        if not settings.notion_api_key:
            raise ValueError("NOTION_API_KEY not configured")
        if not settings.notion_database_id:
            raise ValueError("NOTION_DATABASE_ID not configured")

        self._client = Client(auth=settings.notion_api_key)
        self._db_id = settings.notion_database_id

    # ------------------------------------------------------------------
    # Page creation
    # ------------------------------------------------------------------

    def create_review_page(
        self,
        listing: Any,
        title_variants: list[Any],
        mockup_variants: list[Any],
    ) -> str:
        """Create a Notion review page for *listing* and return the page_id.

        Properties set: Listing Title, Etsy ID, Status, SQLite ID,
        Listing URL, Synced At. Children: title callout blocks + mockup image blocks.
        """
        etsy_url = f"https://www.etsy.com/listing/{listing.etsy_listing_id}"
        synced_at = datetime.now(timezone.utc).isoformat()

        properties = {
            "Listing Title": {"title": [{"text": {"content": listing.original_title[:2000]}}]},
            "Etsy ID": {"number": _safe_int(listing.etsy_listing_id)},
            "Status": {"select": {"name": "review"}},
            "SQLite ID": {"number": listing.id},
            "Listing URL": {"url": etsy_url},
            "Synced At": {"date": {"start": synced_at}},
        }

        children = _build_title_blocks(title_variants) + _build_mockup_blocks(mockup_variants)

        response = self._client.pages.create(
            parent={"database_id": self._db_id},
            properties=properties,
            children=children,
        )
        page_id: str = response["id"]
        logger.info("Created Notion review page %s for listing %d", page_id, listing.id)
        return page_id

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_pages_by_status(self, status: str) -> list[dict]:
        """Return all Notion pages in the database with the given Status select value."""
        results: list[dict] = []
        cursor = None

        while True:
            kwargs: dict[str, Any] = {
                "database_id": self._db_id,
                "filter": {
                    "property": "Status",
                    "select": {"equals": status},
                },
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor

            response = self._client.databases.query(**kwargs)
            results.extend(response.get("results", []))

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        logger.debug("get_pages_by_status(%r): found %d pages", status, len(results))
        return results

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def update_page_status(self, page_id: str, status: str) -> None:
        """Set the Status select property on a Notion page."""
        self._client.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": status}}},
        )
        logger.info("Updated Notion page %s → Status=%r", page_id, status)

    def update_page_selections_done(self, page_id: str) -> None:
        """No-op marker — selections are read by pull_approvals, not written back."""
        # Selections are user-owned in Notion; we only read them from pull_approvals.
        logger.debug("update_page_selections_done: no-op for page %s", page_id)

    # ------------------------------------------------------------------
    # Schema validation (best-effort)
    # ------------------------------------------------------------------

    def validate_database_schema(self) -> bool:
        """Check that the Notion database has the expected properties.

        Returns True if all required properties exist, False otherwise.
        Logs warnings for each missing property but does NOT raise.
        """
        required = {
            "Listing Title", "Etsy ID", "Status", "Selected Title",
            "Selected Mockup", "Listing URL", "Synced At", "SQLite ID",
        }
        try:
            db = self._client.databases.retrieve(database_id=self._db_id)
            existing = set(db.get("properties", {}).keys())
            missing = required - existing
            if missing:
                logger.warning(
                    "Notion DB schema mismatch — missing properties: %s. "
                    "See docs/notion-db-setup.md for setup instructions.",
                    ", ".join(sorted(missing)),
                )
                return False
            logger.info("Notion DB schema validated OK")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notion DB schema validation failed: %s", exc)
            return False


# ------------------------------------------------------------------
# Block builders (private helpers)
# ------------------------------------------------------------------

def _build_title_blocks(variants: list[Any]) -> list[dict]:
    """Build heading + callout blocks for title variants."""
    blocks: list[dict] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Title Variants"}}]},
        }
    ]

    for i, v in enumerate(variants[:3], start=1):
        char_info = f"({v.char_count} chars)" if hasattr(v, "char_count") else ""
        rationale = (v.ai_rationale or "") if hasattr(v, "ai_rationale") else ""
        body = f"Variant {i}: {v.text} {char_info}\n{rationale}".strip()

        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": body[:2000]}}],
                "icon": {"type": "emoji", "emoji": f"{i}️⃣"},
            },
        })

    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {
                "content": "Pick your choice by setting 'Selected Title' property → variant-1 / variant-2 / variant-3"
            }}]
        },
    })
    return blocks


def _build_mockup_blocks(variants: list[Any]) -> list[dict]:
    """Build heading + image blocks for mockup variants."""
    blocks: list[dict] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Mockup Variants"}}]},
        }
    ]

    for i, v in enumerate(variants[:3], start=1):
        prompt_text = (v.scene_prompt or "") if hasattr(v, "scene_prompt") else ""
        caption = f"Variant {i} — {prompt_text}"[:2000]
        image_url = getattr(v, "final_image_url", None) or getattr(v, "final_image_path", None) or ""

        if image_url:
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": image_url},
                    "caption": [{"type": "text", "text": {"content": caption}}],
                },
            })
        else:
            # Fallback: paragraph with placeholder when no public URL available
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {
                        "content": f"Variant {i}: [image not available — R2 upload failed] {caption}"
                    }}]
                },
            })

    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {
                "content": "Pick your choice by setting 'Selected Mockup' property → variant-1 / variant-2 / variant-3"
            }}]
        },
    })
    return blocks


def _safe_int(value: str | int | None) -> int | None:
    """Convert etsy_listing_id (string) to int for Notion Number property, or None."""
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None
