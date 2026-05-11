"""Keyword service — CRUD operations for the keyword table.

All functions take a SQLAlchemy Session as their first argument following
the same convention used across template_service.py, design_service.py, etc.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.keyword import Keyword

logger = logging.getLogger(__name__)


def create_keyword(db: Session, term: str) -> Keyword:
    """Persist a new keyword.

    Args:
        db: Active SQLAlchemy session.
        term: The search term string (stripped of leading/trailing whitespace).

    Returns:
        Newly created Keyword instance.

    Raises:
        ValueError: If a keyword with the same term already exists.
    """
    keyword = Keyword(term=term.strip())
    db.add(keyword)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"Keyword with term {term!r} already exists")
    db.refresh(keyword)
    logger.info("Created keyword id=%d term=%r", keyword.id, keyword.term)
    return keyword


def count_enabled(db: Session) -> int:
    """Return the number of keywords with enabled=True.

    Used by the v0.8.2 quota guard to estimate daily Etsy API load.
    """
    from sqlalchemy import func
    stmt = select(func.count(Keyword.id)).where(Keyword.enabled.is_(True))
    return int(db.scalar(stmt) or 0)


def list_keywords(db: Session, enabled_only: bool = False) -> list[Keyword]:
    """Return all keywords ordered by id ascending.

    Args:
        db: Active SQLAlchemy session.
        enabled_only: When True, only return keywords where enabled=True.

    Returns:
        List of Keyword instances.
    """
    stmt = select(Keyword).order_by(Keyword.id.asc())
    if enabled_only:
        stmt = stmt.where(Keyword.enabled.is_(True))
    return list(db.scalars(stmt).all())


def get_keyword(db: Session, kid: int) -> Keyword | None:
    """Fetch a single keyword by primary key.

    Returns None if not found.
    """
    return db.get(Keyword, kid)


def delete_keyword(db: Session, kid: int) -> None:
    """Delete a keyword by primary key.

    Ideas referencing this keyword will have their keyword_id set to NULL
    (enforced by the FK ON DELETE SET NULL policy).

    Raises:
        ValueError: If keyword not found.
    """
    keyword = db.get(Keyword, kid)
    if keyword is None:
        raise ValueError(f"Keyword {kid} not found")
    db.delete(keyword)
    db.commit()
    logger.info("Deleted keyword id=%d term=%r", kid, keyword.term)


def toggle_keyword(db: Session, kid: int, enabled: bool) -> Keyword:
    """Enable or disable a keyword.

    Args:
        db: Active SQLAlchemy session.
        kid: Primary key of the keyword.
        enabled: New enabled state.

    Returns:
        Updated Keyword instance.

    Raises:
        ValueError: If keyword not found.
    """
    keyword = db.get(Keyword, kid)
    if keyword is None:
        raise ValueError(f"Keyword {kid} not found")
    keyword.enabled = enabled
    db.commit()
    db.refresh(keyword)
    logger.info("Toggled keyword id=%d enabled=%s", kid, enabled)
    return keyword


def touch_last_run(db: Session, kid: int) -> None:
    """Update last_run_at to the current UTC time.

    Called by the miner after successfully processing a keyword.

    Raises:
        ValueError: If keyword not found.
    """
    keyword = db.get(Keyword, kid)
    if keyword is None:
        raise ValueError(f"Keyword {kid} not found")
    keyword.last_run_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    db.commit()
    logger.info("Touched last_run_at for keyword id=%d", kid)
