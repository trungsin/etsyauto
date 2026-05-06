"""Template model — stores product template metadata and composite anchor."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Template(Base):
    """Product template: base image + composite anchor + variation options stored as JSON."""

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # apparel | drinkware | print
    base_image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # JSON: {"x": 0.2, "y": 0.3, "w": 0.6, "h": 0.4} — all values 0-1 float
    composite_anchor_json: Mapped[str] = mapped_column(String(200), nullable=False, default="{}")
    default_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON convention:
    # {"sizes":[{"name":"S","price_cents":1900},...], "colors":[...],
    #  "primary_color":"Sand", "etsy_taxonomy_id":1209}
    variation_options_json: Mapped[str] = mapped_column(String(500), nullable=False, default="{}")
    # JSON map color name → R2 URL: {"White": "https://r2.../tpl-white.png", ...}
    # Keys must match colors listed in variation_options_json.colors.
    # Empty {} means template uses single base_image_url for all colors (v0.2.0 backward compat).
    color_base_images_json: Mapped[str] = mapped_column(String(2000), nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    variations: Mapped[list["TemplateVariation"]] = relationship(  # noqa: F821
        "TemplateVariation", back_populates="template", cascade="all, delete-orphan"
    )
