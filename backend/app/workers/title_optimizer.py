"""Title optimizer worker — APScheduler job that polls new listings and calls Claude."""
import json
import logging

from app.clients.claude_client import ClaudeClient
from app.database import SessionLocal
from app.services import listing_service

logger = logging.getLogger(__name__)


def run_title_optimizer_job() -> None:
    """Poll listings with status='new', call Claude for title variants, persist results.

    Each listing is claimed atomically before processing to prevent duplicate work.
    Exceptions per listing are caught so one failure does not abort the whole batch.
    """
    session = SessionLocal()
    try:
        pending = listing_service.get_pending_for_title(session)
        if not pending:
            return

        logger.info("Title optimizer: found %d pending listing(s)", len(pending))
        client = ClaudeClient()

        for listing in pending:
            _process_listing(session, client, listing)
    finally:
        session.close()


def _process_listing(session, client: ClaudeClient, listing) -> None:
    """Claim and process one listing; log errors without re-raising."""
    listing_id = listing.id

    claimed = listing_service.mark_title_processing(session, listing_id)
    if not claimed:
        logger.info("Listing %d already claimed by another worker — skipping", listing_id)
        return

    try:
        tags_raw = listing.original_tags or "[]"
        try:
            tags_list = json.loads(tags_raw)
            tags_str = ", ".join(tags_list) if isinstance(tags_list, list) else tags_raw
        except (json.JSONDecodeError, TypeError):
            tags_str = tags_raw

        listing_data = {
            "original_title": listing.original_title or "",
            "description": listing.original_desc or "",
            "tags": tags_str,
            "category": "",  # Phase 4: category not yet stored on model
        }

        variants = client.generate_title_variants(listing_data)
        listing_service.save_title_variants(session, listing_id, variants)
        listing_service.mark_title_done(session, listing_id)
        logger.info("Listing %d title optimization complete (%d variants)", listing_id, len(variants))

    except Exception as exc:  # noqa: BLE001 — per-listing isolation, must not crash job
        logger.exception("Title optimization failed for listing %d: %s", listing_id, exc)
        listing_service.mark_title_failed(session, listing_id, str(exc))
