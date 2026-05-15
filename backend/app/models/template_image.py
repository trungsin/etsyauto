"""TemplateImage model — one row per image in a template's image pool."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TemplateImage(Base):
    """Image pool entry for a template: mockup or lifestyle photo with optional color tag."""

    __tablename__ = "template_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anchor_json: Mapped[str] = mapped_column(String(400), nullable=False, default="{}")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="mockup")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    template: Mapped["Template"] = relationship("Template", back_populates="images")  # noqa: F821

    __table_args__ = (
        Index("ix_template_images_template_rank", "template_id", "rank"),
    )
