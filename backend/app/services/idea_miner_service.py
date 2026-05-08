"""Idea miner service — orchestrates Etsy public API search → idea upsert pipeline.

Scheduler entrypoint: run_all(db) iterates all enabled keywords sequentially,
calls EtsyPublicClient to search + fetch listing details, then upserts ideas
and appends engagement signals.

Idempotent: re-running the same keyword upserts (no duplicate ideas) and appends
a new signal row per listing (signals grow as a timeseries).

Fail-closed per listing: a malformed response or HTTP error on one listing is
logged as a warning and skipped; the job never crashes the scheduler.
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy.orm import Session

from app.clients.etsy_public_client import EtsyPublicClient
from app.config import settings
from app.models.keyword import Keyword
from app.services import idea_service, keyword_service

logger = logging.getLogger(__name__)

# Throttle between detail calls — matches Etsy 10/sec public rate limit guidance.
_DETAIL_THROTTLE_SEC = 0.2


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def _map_etsy_to_idea(detail: dict, keyword_id: int) -> dict:
    """Translate an Etsy listing detail dict to idea_service.upsert_idea kwargs.

    All fields are extracted defensively with .get() to handle absent keys from
    the Etsy API gracefully (ref: phase-02 risk assessment — absent price/images).

    Args:
        detail: Full listing detail dict from EtsyPublicClient.get_listing.
        keyword_id: FK to the Keyword that triggered this mining run.

    Returns:
        Dict of keyword arguments suitable for idea_service.upsert_idea(**returned).
    """
    # Price: convert Etsy {amount, divisor} to cents integer
    price_cents: int | None = None
    price_data = detail.get("price")
    if price_data and price_data.get("amount") is not None:
        try:
            # Etsy stores price as integer cents when divisor=100, or as raw units
            divisor = price_data.get("divisor") or 100
            price_cents = int(round(float(price_data["amount"]) / divisor * 100))
        except (TypeError, ValueError, ZeroDivisionError):
            logger.debug("miner: could not parse price for listing %s", detail.get("listing_id"))

    # Primary image URL — first image in list if present
    images = detail.get("images") or []
    reference_image_url: str | None = None
    if images and isinstance(images[0], dict):
        reference_image_url = images[0].get("url_fullxfull")

    return {
        "source": "etsy_api",
        "source_listing_id": str(detail["listing_id"]),
        "keyword_id": keyword_id,
        "title": detail.get("title"),
        "description": detail.get("description"),
        "tags_json": json.dumps(detail.get("tags") or []),
        "materials_json": json.dumps(detail.get("materials") or []),
        "taxonomy_id": detail.get("taxonomy_id"),
        "who_made": detail.get("who_made"),
        "when_made": detail.get("when_made"),
        "reference_image_url": reference_image_url,
        "price_cents": price_cents,
    }


# ---------------------------------------------------------------------------
# Single-keyword miner
# ---------------------------------------------------------------------------


def run_for_keyword(
    db: Session,
    keyword_id: int,
    client: EtsyPublicClient | None = None,
) -> dict:
    """Mine one keyword: search → fetch details → upsert ideas + append signals.

    Args:
        db: Active SQLAlchemy session.
        keyword_id: Primary key of the keyword to mine.
        client: Optional pre-built EtsyPublicClient (useful for testing / dry-run).
                If None, a new client is constructed from settings.

    Returns:
        Result dict: {keyword, ideas_upserted, signals_appended, errors}.
        Returns early with zeros if keyword not found or disabled.
    """
    kw = keyword_service.get_keyword(db, keyword_id)
    if kw is None:
        logger.warning("miner: keyword id=%d not found, skipping", keyword_id)
        return {"keyword": None, "ideas_upserted": 0, "signals_appended": 0, "errors": 0}

    if not kw.enabled:
        logger.info("miner: keyword id=%d %r disabled, skipping", keyword_id, kw.term)
        return {"keyword": kw.term, "ideas_upserted": 0, "signals_appended": 0, "errors": 0}

    own_client = client is None
    if own_client:
        client = EtsyPublicClient()

    ideas_upserted = 0
    signals_appended = 0
    errors = 0

    try:
        summaries = client.search_active_listings(
            kw.term,
            limit=settings.etsy_miner_per_keyword_limit,
        )
        logger.info(
            "miner: keyword=%r returned %d summaries", kw.term, len(summaries)
        )

        for summary in summaries:
            listing_id = summary.get("listing_id")
            try:
                detail = client.get_listing(str(listing_id))
                time.sleep(_DETAIL_THROTTLE_SEC)

                idea_fields = _map_etsy_to_idea(detail, keyword_id=kw.id)
                idea, _ = idea_service.upsert_idea(db, **idea_fields)

                idea_service.append_signal(
                    db,
                    idea.id,
                    num_favorers=detail.get("num_favorers"),
                    views_all_time=detail.get("views_all_time"),
                )
                ideas_upserted += 1
                signals_appended += 1

            except Exception as exc:
                logger.warning(
                    "miner: skip listing=%s keyword=%r: %s",
                    listing_id, kw.term, exc,
                )
                errors += 1

        keyword_service.touch_last_run(db, kw.id)

    finally:
        if own_client and client is not None:
            client.close()

    result = {
        "keyword": kw.term,
        "ideas_upserted": ideas_upserted,
        "signals_appended": signals_appended,
        "errors": errors,
    }
    logger.info("miner: completed keyword=%r result=%s", kw.term, result)
    return result


# ---------------------------------------------------------------------------
# Scheduler entrypoint
# ---------------------------------------------------------------------------


def run_all(db: Session | None = None) -> None:
    """Mine all enabled keywords. Scheduler entrypoint — opens own DB session if needed.

    Logs a summary dict {keywords_processed, ideas_upserted, errors} after all keywords.
    Fail-closed: individual keyword failures are caught and counted; job never crashes.

    Args:
        db: Optional SQLAlchemy session. When None, a new session is opened and
            closed after the run (standard scheduler usage).
    """
    from app.database import SessionLocal  # local import avoids circular deps at module load

    own_session = db is None
    if own_session:
        db = SessionLocal()

    keywords_processed = 0
    total_ideas = 0
    total_errors = 0

    client = EtsyPublicClient()

    try:
        keywords = keyword_service.list_keywords(db, enabled_only=True)
        logger.info("miner: starting run_all — %d enabled keyword(s)", len(keywords))

        for kw in keywords:
            try:
                result = run_for_keyword(db, kw.id, client=client)
                keywords_processed += 1
                total_ideas += result.get("ideas_upserted", 0)
                total_errors += result.get("errors", 0)
            except Exception as exc:
                logger.error("miner: uncaught error for keyword id=%d: %s", kw.id, exc)
                total_errors += 1

        summary = {
            "keywords_processed": keywords_processed,
            "ideas_upserted": total_ideas,
            "errors": total_errors,
        }
        logger.info("miner: run_all complete — %s", summary)

    finally:
        client.close()
        if own_session:
            db.close()
