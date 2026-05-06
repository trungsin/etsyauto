"""Listing service — DB operations for status transitions and variant persistence."""
import json
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
MODEL_ID = "claude-sonnet-4-6"


def get_pending_for_title(session: Session) -> list[Listing]:
    """Return all listings with status='new'."""
    return session.query(Listing).filter(Listing.status == "new").all()


def mark_title_processing(session: Session, listing_id: int) -> bool:
    """Atomically claim a listing for title processing.

    Uses UPDATE...WHERE status='new' to prevent two workers picking the same row.
    Returns True if this worker claimed it, False if already claimed.
    """
    result = session.execute(
        update(Listing)
        .where(Listing.id == listing_id, Listing.status == "new")
        .values(status="title-processing")
    )
    session.commit()
    return result.rowcount == 1


def save_title_variants(session: Session, listing_id: int, variants: list[dict]) -> None:
    """Insert title variant rows for a listing.

    Idempotent: skips insert if variants already exist for this listing.
    """
    existing = (
        session.query(TitleVariant)
        .filter(TitleVariant.listing_id == listing_id)
        .count()
    )
    if existing > 0:
        logger.info("Variants already exist for listing %d — skipping insert", listing_id)
        return

    for variant in variants:
        row = TitleVariant(
            listing_id=listing_id,
            text=variant["text"],
            char_count=variant["char_count"],
            model=MODEL_ID,
            prompt_version=PROMPT_VERSION,
            ai_rationale=variant.get("rationale", ""),
            selected=False,
        )
        session.add(row)

    session.commit()
    logger.info("Saved %d title variants for listing %d", len(variants), listing_id)


def mark_title_done(session: Session, listing_id: int) -> None:
    """Transition listing status to 'mockup-pending'."""
    session.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values(status="mockup-pending")
    )
    session.commit()


def get_pending_for_mockup(session: Session) -> list[Listing]:
    """Return all listings with status='mockup-pending'."""
    return session.query(Listing).filter(Listing.status == "mockup-pending").all()


def mark_mockup_processing(session: Session, listing_id: int) -> bool:
    """Atomically claim a listing for mockup processing.

    Uses UPDATE...WHERE status='mockup-pending' to prevent duplicate work.
    Returns True if this worker claimed it, False if already claimed.
    """
    result = session.execute(
        update(Listing)
        .where(Listing.id == listing_id, Listing.status == "mockup-pending")
        .values(status="mockup-processing")
    )
    session.commit()
    return result.rowcount == 1


def save_mockup_variants(session: Session, listing_id: int, variants: list[dict]) -> None:
    """Insert mockup variant rows for a listing.

    Each dict must have: source_image_url, removed_bg_path, final_image_path,
    scene_prompt, model.
    Idempotent: skips insert if variants already exist for this listing.
    """
    existing = (
        session.query(MockupVariant)
        .filter(MockupVariant.listing_id == listing_id)
        .count()
    )
    if existing > 0:
        logger.info("Mockup variants already exist for listing %d — skipping insert", listing_id)
        return

    for variant in variants:
        row = MockupVariant(
            listing_id=listing_id,
            source_image_url=variant.get("source_image_url"),
            removed_bg_path=variant.get("removed_bg_path"),
            final_image_path=variant.get("final_image_path"),
            final_image_url=variant.get("final_image_url"),
            scene_prompt=variant.get("scene_prompt"),
            model=variant.get("model", ""),
            selected=False,
        )
        session.add(row)

    session.commit()
    logger.info("Saved %d mockup variants for listing %d", len(variants), listing_id)


def try_promote_to_review(session: Session, listing_id: int) -> bool:
    """Transition listing to 'review' only when BOTH title AND mockup variants exist.

    Checks:
    - >= 3 title_variants rows for this listing
    - >= 3 mockup_variants rows for this listing

    Returns True if promoted, False if conditions not yet met.
    """
    title_count = (
        session.query(TitleVariant)
        .filter(TitleVariant.listing_id == listing_id)
        .count()
    )
    mockup_count = (
        session.query(MockupVariant)
        .filter(MockupVariant.listing_id == listing_id)
        .count()
    )

    if title_count >= 3 and mockup_count >= 3:
        session.execute(
            update(Listing)
            .where(Listing.id == listing_id)
            .values(status="review")
        )
        session.commit()
        logger.info(
            "Listing %d promoted to 'review' (title_variants=%d, mockup_variants=%d)",
            listing_id,
            title_count,
            mockup_count,
        )
        return True

    logger.info(
        "Listing %d not yet ready for review (title_variants=%d, mockup_variants=%d)",
        listing_id,
        title_count,
        mockup_count,
    )
    return False


def mark_mockup_failed(session: Session, listing_id: int, error: str) -> None:
    """Transition listing status to 'failed' after mockup processing error."""
    session.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values(status="failed")
    )
    session.commit()
    logger.error("Listing %d mockup failed: %s", listing_id, error)


def mark_title_failed(session: Session, listing_id: int, error: str) -> None:
    """Transition listing status to 'failed' with error stored in original_desc fallback.

    Stores truncated error message in a dedicated field if available; otherwise logs it.
    """
    # Store error as JSON prefix in original_desc is too invasive — just log and update status.
    # Future: add last_error column via migration.
    session.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values(status="failed")
    )
    session.commit()
    logger.error("Listing %d marked failed: %s", listing_id, error)
