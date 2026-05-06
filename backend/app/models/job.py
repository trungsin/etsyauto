"""Job model — tracks async task execution state."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # type: title | mockup | push
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # status: pending | running | done | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
