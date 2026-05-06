"""Etsy uploader worker — APScheduler job that pushes approved listings to Etsy.

Flow per cron tick:
  1. Atomically claim one listing with status='approved' → set status='pushing'
  2. Fetch selected TitleVariant and MockupVariant
  3. PATCH listing title via Etsy API
  4. POST mockup image (rank=1) via Etsy API
  5. On success: status='pushed', pushed_at=now(), notify Notion
  6. On exception: increment push_attempts.
     - attempts < 3: revert to status='approved' (picked up on next tick)
     - attempts >= 3: status='failed', persist error, log Notion comment

Tradeoff note: retrying by reverting to 'approved' means the delay between
retries is the cron interval (10 min), not the exponential backoff delays
(5/30/300s). This is simpler than scheduling a delayed job and acceptable
because Etsy API errors are usually transient.
"""
import logging
from datetime import datetime, timezone

from app.clients.etsy_api_client import EtsyApiClient
from app.clients.notion_client import NotionClient
from app.database import SessionLocal
from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant
from app.services.retry_policy import should_retry

logger = logging.getLogger(__name__)

MAX_PUSH_ATTEMPTS = 3


def run_etsy_uploader_job() -> dict:
    """Poll one approved listing and push it to Etsy.

    Returns a dict with keys: listing_id, status, error (if any).
    Designed to be called by APScheduler and the admin endpoint.
    """
    session = SessionLocal()
    try:
        return _claim_and_push(session)
    finally:
        session.close()


def _claim_and_push(session) -> dict:
    """Atomically claim one approved listing and attempt the Etsy push."""
    # Atomic claim: SELECT + UPDATE with rowcount guard (SQLite-safe)
    listing = (
        session.query(Listing)
        .filter(Listing.status == "approved")
        .with_for_update()
        .first()
    )

    if listing is None:
        logger.debug("etsy_uploader: no approved listings to push")
        return {"listing_id": None, "status": "idle", "error": None}

    listing_id = listing.id
    etsy_id = listing.etsy_listing_id

    # Claim: set status to 'pushing' so concurrent ticks don't double-pick
    listing.status = "pushing"
    session.commit()
    logger.info("etsy_uploader: claimed listing %d (etsy_id=%s)", listing_id, etsy_id)

    try:
        title_variant = _get_selected_title(session, listing_id)
        mockup_variant = _get_selected_mockup(session, listing_id)

        with EtsyApiClient(session) as etsy:
            etsy.update_listing(etsy_id, title=title_variant.text)
            logger.info("etsy_uploader: updated title for listing %d", listing_id)

            if mockup_variant.final_image_path:
                etsy.upload_listing_image(etsy_id, mockup_variant.final_image_path, rank=1)
                logger.info("etsy_uploader: uploaded image for listing %d", listing_id)
            else:
                logger.warning(
                    "etsy_uploader: listing %d has no final_image_path — skipping image upload",
                    listing_id,
                )

        # Success
        session.refresh(listing)
        listing.status = "pushed"
        listing.pushed_at = datetime.now(timezone.utc)
        listing.last_push_error = None
        session.commit()
        logger.info("etsy_uploader: listing %d pushed successfully", listing_id)

        _notify_notion_pushed(listing)

        return {"listing_id": listing_id, "status": "pushed", "error": None}

    except Exception as exc:  # noqa: BLE001 — isolation: one listing failure must not crash job
        error_msg = str(exc)
        logger.exception("etsy_uploader: push failed for listing %d: %s", listing_id, error_msg)

        session.refresh(listing)
        listing.push_attempts = (listing.push_attempts or 0) + 1
        listing.last_push_error = error_msg[:2000]

        if should_retry(listing.push_attempts, MAX_PUSH_ATTEMPTS):
            # Revert to 'approved' so the next cron tick will retry
            listing.status = "approved"
            logger.info(
                "etsy_uploader: listing %d reverted to approved (attempt %d/%d)",
                listing_id, listing.push_attempts, MAX_PUSH_ATTEMPTS,
            )
        else:
            # Max attempts reached — mark as failed
            listing.status = "failed"
            logger.error(
                "etsy_uploader: listing %d failed after %d attempts — marking failed",
                listing_id, listing.push_attempts,
            )
            _notify_notion_failed(listing, error_msg)

        session.commit()
        return {"listing_id": listing_id, "status": listing.status, "error": error_msg}


def _get_selected_title(session, listing_id: int) -> TitleVariant:
    """Return the selected TitleVariant; raise if none found."""
    variant = (
        session.query(TitleVariant)
        .filter(TitleVariant.listing_id == listing_id, TitleVariant.selected.is_(True))
        .first()
    )
    if variant is None:
        raise ValueError(f"No selected title variant for listing {listing_id}")
    return variant


def _get_selected_mockup(session, listing_id: int) -> MockupVariant:
    """Return the selected MockupVariant; raise if none found."""
    variant = (
        session.query(MockupVariant)
        .filter(MockupVariant.listing_id == listing_id, MockupVariant.selected.is_(True))
        .first()
    )
    if variant is None:
        raise ValueError(f"No selected mockup variant for listing {listing_id}")
    return variant


def _notify_notion_pushed(listing: Listing) -> None:
    """Update Notion page status to 'Pushed' (best-effort, non-fatal)."""
    if not listing.notion_page_id:
        return
    try:
        notion = NotionClient()
        notion.update_page_status(listing.notion_page_id, "Pushed")
        logger.info("etsy_uploader: Notion page %s → Pushed", listing.notion_page_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("etsy_uploader: Notion update failed (non-fatal): %s", exc)


def _notify_notion_failed(listing: Listing, error_msg: str) -> None:
    """Log failure to Notion by updating status to 'Failed' (best-effort, non-fatal)."""
    if not listing.notion_page_id:
        return
    try:
        notion = NotionClient()
        notion.update_page_status(listing.notion_page_id, "Failed")
        logger.info(
            "etsy_uploader: Notion page %s → Failed (error: %s)",
            listing.notion_page_id, error_msg[:200],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("etsy_uploader: Notion failure notification failed (non-fatal): %s", exc)
