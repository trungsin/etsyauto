"""Idea model — a candidate product idea discovered by the miner or extension."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Idea(Base):
    """A product idea sourced from Etsy API search, extension passive log, or Printful."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Source system that produced this idea: etsy_api | extension_passive | printful
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    # Listing ID within the source system — unique per source
    source_listing_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional link back to the keyword that triggered discovery; SET NULL on keyword delete
    keyword_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("keywords.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # JSON-encoded list of tag strings, e.g. ["gift","personalized"]
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded list of material strings
    materials_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Etsy taxonomy node ID (category)
    taxonomy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Etsy "who_made" field, e.g. "i_did", "collective", "someone_else"
    who_made: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Etsy "when_made" field, e.g. "made_to_order", "2020_2024"
    when_made: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Primary listing image URL
    reference_image_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Price in cents (integer arithmetic avoids float rounding)
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Workflow status: new | reviewed | rejected | drafted
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Dedup constraint: same listing from the same source is always one row
        UniqueConstraint("source", "source_listing_id", name="uq_ideas_source_pair"),
        # Fast status-based filtering for the admin UI
        Index("idx_ideas_status", "status"),
        # Fast keyword-scoped queries in the miner output view
        Index("idx_ideas_keyword", "keyword_id"),
    )
