"""Reference model — stores scraped Etsy listing references for inspiration."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Reference(Base):
    """Scraped Etsy listing saved as a design reference."""

    __tablename__ = "references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded list of image URLs from source listing
    original_images_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON-encoded list of 3 AI-suggested title variants (populated in Phase 2)
    ai_title_variants_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded list of tag strings, e.g. ["style","color"]
    tags_json: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # JSON-encoded list of kept image indices, e.g. [0, 2]
    kept_image_indices_json: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Optional link to a cutout Design row — SET NULL on design delete
    cutout_design_id: Mapped[int | None] = mapped_column(
        ForeignKey("designs.id", ondelete="SET NULL"), nullable=True
    )
    # Notion page ID for this reference (populated in Phase 3)
    notion_page_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Status: scraped → enriched → saved
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scraped")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
