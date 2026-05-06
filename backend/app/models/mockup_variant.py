"""MockupVariant model — processed mockup image candidates for a listing."""
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MockupVariant(Base):
    __tablename__ = "mockup_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    removed_bg_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    final_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Cloudflare R2 public URL — populated after upload; None when R2 not configured
    final_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scene_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
