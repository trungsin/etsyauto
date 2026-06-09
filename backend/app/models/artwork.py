"""Artwork model — tracks each step in the Cloden Design POD artwork pipeline.

Status machine: pending → cropped → refining → refined → removebg_done → upscaling → done | failed | refine_failed
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Artwork(Base):
    __tablename__ = "artworks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # pending | cropped | refining | refined | refine_failed | removebg_done | upscaling | done | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cropped_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refined_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    removebg_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    final_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
