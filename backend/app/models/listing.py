"""Listing model — mirrors an Etsy listing with optimization state."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etsy_listing_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    original_title: Mapped[str] = mapped_column(String(512), nullable=False)
    original_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialized list of strings
    original_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialized list of image URLs
    original_images: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status: new | title-processing | mockup-pending | mockup-processing | review | approved | pushing | pushed | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Notion page ID — set after sync_to_notion creates the review page
    notion_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Push tracking — populated by etsy_uploader worker
    push_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_push_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
