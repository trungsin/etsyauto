"""Idea service — CRUD + analytics operations for ideas, signals, and idea-to-listing links.

All functions take a SQLAlchemy Session as their first argument following
the same convention used across template_service.py and design_service.py.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.idea import Idea
from app.models.idea_signal import IdeaSignal
from app.models.idea_to_listing import IdeaToListing

logger = logging.getLogger(__name__)

# Fields that are safe to overwrite on an existing idea during upsert.
# `status` is intentionally excluded so user review/reject decisions are preserved.
_MUTABLE_FIELDS = frozenset(
    {"title", "tags_json", "description", "materials_json", "reference_image_url", "price_cents"}
)


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------


def upsert_idea(
    db: Session,
    *,
    source: str,
    source_listing_id: str,
    **fields,
) -> tuple[Idea, bool]:
    """Insert or update an idea keyed on (source, source_listing_id).

    On conflict:
    - Mutable fields (title, tags_json, description, materials_json,
      reference_image_url, price_cents) are updated.
    - `status` is never overwritten — preserves user reject/draft decisions.
    - Immutable fields (source, source_listing_id, keyword_id, created_at)
      are left unchanged on update.

    Args:
        db: Active SQLAlchemy session.
        source: Source system identifier, e.g. "etsy_api".
        source_listing_id: Listing ID within the source system.
        **fields: Optional idea fields to set/update.

    Returns:
        (idea, created) where created=True means a new row was inserted.
    """
    stmt = select(Idea).where(
        Idea.source == source,
        Idea.source_listing_id == source_listing_id,
    )
    idea = db.scalars(stmt).first()

    if idea is None:
        # Insert new row — all provided fields go in
        idea = Idea(source=source, source_listing_id=source_listing_id, **fields)
        db.add(idea)
        db.commit()
        db.refresh(idea)
        logger.info("Inserted idea id=%d source=%s slid=%s", idea.id, source, source_listing_id)
        return idea, True

    # Update only mutable fields that were supplied
    changed = False
    for key, value in fields.items():
        if key in _MUTABLE_FIELDS:
            setattr(idea, key, value)
            changed = True

    if changed:
        db.commit()
        db.refresh(idea)
        logger.info("Updated idea id=%d source=%s slid=%s", idea.id, source, source_listing_id)

    return idea, False


def get_idea(db: Session, iid: int) -> Idea | None:
    """Fetch a single idea by primary key. Returns None if not found."""
    return db.get(Idea, iid)


def reject_idea(db: Session, iid: int) -> Idea:
    """Mark an idea as rejected.

    Raises:
        ValueError: If idea not found.
    """
    idea = db.get(Idea, iid)
    if idea is None:
        raise ValueError(f"Idea {iid} not found")
    idea.status = "rejected"
    db.commit()
    db.refresh(idea)
    logger.info("Rejected idea id=%d", iid)
    return idea


def mark_drafted(db: Session, iid: int) -> Idea:
    """Mark an idea as drafted (listing wizard submitted for it).

    Raises:
        ValueError: If idea not found.
    """
    idea = db.get(Idea, iid)
    if idea is None:
        raise ValueError(f"Idea {iid} not found")
    idea.status = "drafted"
    db.commit()
    db.refresh(idea)
    logger.info("Marked idea id=%d as drafted", iid)
    return idea


# ---------------------------------------------------------------------------
# List + sort
# ---------------------------------------------------------------------------


def count_by_keyword(db: Session, keyword_id: int) -> int:
    """Return the count of ideas linked to a given keyword.

    Args:
        db: Active SQLAlchemy session.
        keyword_id: Primary key of the keyword to count ideas for.

    Returns:
        Integer count of ideas with that keyword_id.
    """
    from sqlalchemy import func as sa_func  # local import avoids top-level name clash

    stmt = select(sa_func.count()).select_from(Idea).where(Idea.keyword_id == keyword_id)
    result = db.execute(stmt).scalar_one()
    return int(result)


def list_signals(db: Session, iid: int, limit: int = 20) -> list[IdeaSignal]:
    """Return the most recent *limit* signals for an idea, newest first.

    Args:
        db: Active SQLAlchemy session.
        iid: Primary key of the idea.
        limit: Maximum number of signal rows to return.

    Returns:
        List of IdeaSignal instances sorted descending by ts.
    """
    stmt = (
        select(IdeaSignal)
        .where(IdeaSignal.idea_id == iid)
        .order_by(IdeaSignal.ts.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def list_ideas(
    db: Session,
    *,
    keyword_id: int | None = None,
    status: str | None = None,
    source: str | None = None,
    sort: str = "recency",
    limit: int = 100,
    offset: int = 0,
) -> list[Idea]:
    """Return ideas filtered by optional criteria and sorted.

    Sort options:
    - ``recency``  — newest first by created_at (default).
    - ``favorers`` — highest latest num_favorers first; ideas without signals last.
    - ``velocity`` — highest favorer-growth-per-hour first; computed in Python.

    Args:
        db: Active SQLAlchemy session.
        keyword_id: If set, only return ideas linked to this keyword.
        status: If set, only return ideas with this status value.
        source: If set, only return ideas from this source system.
        sort: Sort key — "recency", "favorers", or "velocity".
        limit: Max rows to return.
        offset: Pagination offset.

    Returns:
        List of Idea instances (sorted, sliced).
    """
    stmt = select(Idea)
    if keyword_id is not None:
        stmt = stmt.where(Idea.keyword_id == keyword_id)
    if status is not None:
        stmt = stmt.where(Idea.status == status)
    if source is not None:
        stmt = stmt.where(Idea.source == source)

    if sort == "recency":
        stmt = stmt.order_by(Idea.created_at.desc()).offset(offset).limit(limit)
        return list(db.scalars(stmt).all())

    # For velocity and favorers we need signals — fetch all matching ideas first
    # then sort in Python. Volume is low enough at v0.8 scale (hundreds of ideas).
    all_ideas = list(db.scalars(stmt).all())

    if sort == "favorers":
        def _latest_favorers(idea: Idea) -> int:
            sig = _latest_signal_for(db, idea.id)
            return sig.num_favorers if (sig and sig.num_favorers is not None) else -1

        all_ideas.sort(key=_latest_favorers, reverse=True)

    elif sort == "velocity":
        def _velocity(idea: Idea) -> float:
            return compute_velocity(db, idea.id)

        all_ideas.sort(key=_velocity, reverse=True)

    return all_ideas[offset: offset + limit]


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def append_signal(
    db: Session,
    iid: int,
    *,
    num_favorers: int | None,
    views_all_time: int | None,
) -> IdeaSignal:
    """Append a new engagement snapshot for an idea.

    Args:
        db: Active SQLAlchemy session.
        iid: Primary key of the idea.
        num_favorers: Favorer count at snapshot time (None if unavailable).
        views_all_time: View count at snapshot time (None if unavailable).

    Returns:
        Newly created IdeaSignal row.

    Raises:
        ValueError: If idea not found.
    """
    idea = db.get(Idea, iid)
    if idea is None:
        raise ValueError(f"Idea {iid} not found")
    signal = IdeaSignal(idea_id=iid, num_favorers=num_favorers, views_all_time=views_all_time)
    db.add(signal)
    db.commit()
    db.refresh(signal)
    logger.debug("Appended signal id=%d to idea id=%d", signal.id, iid)
    return signal


def latest_signal(db: Session, iid: int) -> IdeaSignal | None:
    """Return the most recent signal for an idea, or None if no signals exist."""
    return _latest_signal_for(db, iid)


def _latest_signal_for(db: Session, iid: int) -> IdeaSignal | None:
    """Internal helper — most recent signal for idea `iid`."""
    stmt = (
        select(IdeaSignal)
        .where(IdeaSignal.idea_id == iid)
        .order_by(IdeaSignal.ts.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def compute_velocity(db: Session, iid: int) -> float:
    """Compute favorer growth rate (favorers per hour) for an idea.

    Uses the earliest and latest signals.  Returns 0.0 if:
    - Fewer than 2 signals exist.
    - Either endpoint has a NULL num_favorers.
    - Time delta is zero or negative (clock skew / same-second inserts).

    Args:
        db: Active SQLAlchemy session.
        iid: Primary key of the idea.

    Returns:
        Float favorers per hour (>= 0.0).
    """
    stmt = (
        select(IdeaSignal)
        .where(IdeaSignal.idea_id == iid)
        .order_by(IdeaSignal.ts.asc())
    )
    signals = list(db.scalars(stmt).all())

    if len(signals) < 2:
        return 0.0

    earliest, latest = signals[0], signals[-1]
    if earliest.num_favorers is None or latest.num_favorers is None:
        return 0.0

    # Ensure both ts values are timezone-naive for arithmetic consistency
    def _naive(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

    delta_seconds = (_naive(latest.ts) - _naive(earliest.ts)).total_seconds()
    if delta_seconds <= 0:
        return 0.0

    delta_favorers = latest.num_favorers - earliest.num_favorers
    return max(0.0, delta_favorers / (delta_seconds / 3600))


# ---------------------------------------------------------------------------
# Idea-to-listing link
# ---------------------------------------------------------------------------


def set_design_id(db: Session, idea_id: int, design_id: int) -> None:
    """Set idea.design_id and commit.

    Args:
        db: Active SQLAlchemy session.
        idea_id: Primary key of the idea to update.
        design_id: Primary key of the design to link.

    Raises:
        ValueError: If idea not found.
    """
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise ValueError(f"Idea {idea_id} not found")
    idea.design_id = design_id
    db.commit()
    logger.info("Set idea id=%d design_id=%d", idea_id, design_id)


def link_to_listing(db: Session, iid: int, listing_id: int) -> IdeaToListing:
    """Link an idea to a listing (idempotent — safe to call multiple times).

    Returns the existing row if the link already exists, otherwise creates it.

    Raises:
        ValueError: If idea not found.
    """
    idea = db.get(Idea, iid)
    if idea is None:
        raise ValueError(f"Idea {iid} not found")

    existing = db.get(IdeaToListing, (iid, listing_id))
    if existing is not None:
        return existing

    link = IdeaToListing(idea_id=iid, listing_id=listing_id)
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race condition: another process inserted first — fetch and return
        existing = db.get(IdeaToListing, (iid, listing_id))
        if existing:
            return existing
        raise
    db.refresh(link)
    logger.info("Linked idea id=%d to listing id=%d", iid, listing_id)
    return link
