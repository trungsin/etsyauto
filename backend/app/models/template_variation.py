"""TemplateVariation model — one row per (template, size, color) combination."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TemplateVariation(Base):
    """Concrete variation of a template identified by size + color."""

    __tablename__ = "template_variations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)

    template: Mapped["Template"] = relationship("Template", back_populates="variations")  # noqa: F821

    __table_args__ = (UniqueConstraint("template_id", "size", "color", name="uq_variation_template_size_color"),)
