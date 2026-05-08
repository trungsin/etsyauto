"""Keyword model — search terms used by the Etsy miner to discover trending ideas."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Keyword(Base):
    """A search keyword that drives the idea miner job."""

    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The search term — must be unique across the table
    term: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    # Whether the miner should run for this keyword
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # UTC timestamp of the last successful miner run
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
