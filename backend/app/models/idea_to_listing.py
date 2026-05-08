"""IdeaToListing model — join table linking ideas to the listings they produced."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IdeaToListing(Base):
    """Many-to-many join between ideas and listings.

    An idea can spawn multiple listings (e.g., different colorways).
    A listing is always traceable back to the idea that inspired it.
    Composite PK (idea_id, listing_id) enforces uniqueness at the DB level.
    Both FKs CASCADE so rows are cleaned up when either parent is deleted.
    """

    __tablename__ = "idea_to_listing"

    idea_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
