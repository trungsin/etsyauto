"""Notion sync worker — bidirectional sync between SQLite and Notion review DB."""
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.clients.notion_client import NotionClient
from app.database import SessionLocal
from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant
from app.services.review_service import mark_variants_selected

logger = logging.getLogger(__name__)


def sync_to_notion() -> None:
    """Push listings in status='review' that have no notion_page_id to Notion.

    Idempotent: listings where notion_page_id is already set are skipped.
    Errors per listing are caught so one failure does not abort the batch.
    """
    session = SessionLocal()
    try:
        pending = (
            session.query(Listing)
            .filter(Listing.status == "review", Listing.notion_page_id.is_(None))
            .all()
        )
        if not pending:
            return

        logger.info("sync_to_notion: %d listing(s) to sync", len(pending))
        client = _get_notion_client()
        if client is None:
            return

        for listing in pending:
            _sync_one_listing(session, client, listing)
    finally:
        session.close()


def pull_approvals() -> None:
    """Fetch Notion pages with Status='Approved', read selections, update SQLite.

    Only processes pages with both Selected Title and Selected Mockup set.
    Errors per page are caught to isolate failures.
    """
    session = SessionLocal()
    try:
        client = _get_notion_client()
        if client is None:
            return

        pages = client.get_pages_by_status("Approved")
        if not pages:
            return

        logger.info("pull_approvals: %d approved page(s) found", len(pages))
        for page in pages:
            _process_approval(session, client, page)
    finally:
        session.close()


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _get_notion_client() -> NotionClient | None:
    """Instantiate NotionClient; return None (and warn) if not configured."""
    try:
        return NotionClient()
    except ValueError as exc:
        logger.warning("Notion not configured — skipping sync: %s", exc)
        return None


def _sync_one_listing(session: Session, client: NotionClient, listing: Listing) -> None:
    """Create a Notion page for one listing and persist the page_id."""
    listing_id = listing.id
    try:
        title_variants = (
            session.query(TitleVariant)
            .filter(TitleVariant.listing_id == listing_id)
            .order_by(TitleVariant.id)
            .all()
        )
        mockup_variants = (
            session.query(MockupVariant)
            .filter(MockupVariant.listing_id == listing_id)
            .order_by(MockupVariant.id)
            .all()
        )

        page_id = client.create_review_page(listing, title_variants, mockup_variants)

        session.execute(
            update(Listing)
            .where(Listing.id == listing_id)
            .values(notion_page_id=page_id)
        )
        session.commit()
        logger.info("sync_to_notion: listing %d → Notion page %s", listing_id, page_id)

    except Exception as exc:  # noqa: BLE001 — per-listing isolation
        logger.exception("sync_to_notion: failed for listing %d: %s", listing_id, exc)


def _process_approval(session: Session, client: NotionClient, page: dict) -> None:
    """Read Notion page selections and update SQLite if both fields are set."""
    page_id = page.get("id", "unknown")
    try:
        props = page.get("properties", {})

        sqlite_id = _get_number(props, "SQLite ID")
        if sqlite_id is None:
            logger.warning("pull_approvals: page %s has no SQLite ID — skipping", page_id)
            return

        listing_id = int(sqlite_id)

        # Check listing not already approved (avoid re-processing)
        listing = session.get(Listing, listing_id)
        if listing is None:
            logger.warning("pull_approvals: listing %d not found in SQLite", listing_id)
            return
        if listing.status == "approved":
            logger.debug("pull_approvals: listing %d already approved — skipping", listing_id)
            return

        # Property names match the live Notion DB exactly (" Selected Title" has a leading space)
        title_sel = _get_select(props, " Selected Title")
        mockup_sel = _get_select(props, "Selected Mockup")

        if not title_sel or not mockup_sel:
            logger.debug(
                "pull_approvals: page %s missing selections (title=%r, mockup=%r) — skipping",
                page_id,
                title_sel,
                mockup_sel,
            )
            return

        mark_variants_selected(session, listing_id, title_sel, mockup_sel)
        logger.info(
            "pull_approvals: listing %d approved (title=%r, mockup=%r)",
            listing_id,
            title_sel,
            mockup_sel,
        )

    except Exception as exc:  # noqa: BLE001 — per-page isolation
        logger.exception("pull_approvals: failed for page %s: %s", page_id, exc)


def _get_select(props: dict, key: str) -> str | None:
    """Extract a select property value by key from Notion page properties."""
    prop = props.get(key, {})
    select = prop.get("select")
    if select is None:
        return None
    return select.get("name")


def _get_number(props: dict, key: str) -> float | None:
    """Extract a number property value by key from Notion page properties."""
    prop = props.get(key, {})
    return prop.get("number")
