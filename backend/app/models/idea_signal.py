"""IdeaSignal model — timeseries snapshot of engagement metrics for an idea."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdeaSignal(Base):
    """Point-in-time snapshot of favorer count and view count for an Idea.

    Each time the miner re-encounters a known idea it appends a new row here.
    Velocity is computed in Python by diffing consecutive rows.
    """

    __tablename__ = "idea_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Owning idea — cascade delete ensures orphan rows are cleaned up automatically
    idea_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False
    )
    # When the snapshot was taken (server-side default so miner doesn't need to supply it)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    # Number of users who favorited the listing at snapshot time
    num_favorers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Total all-time view count at snapshot time
    views_all_time: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # Allows efficient "fetch all signals for idea ordered by time" queries
        Index("idx_signals_idea_ts", "idea_id", "ts"),
    )
