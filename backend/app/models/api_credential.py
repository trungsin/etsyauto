"""ApiCredential model — stores OAuth tokens keyed by provider name."""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiCredential(Base):
    __tablename__ = "api_credentials"

    # provider is the primary key: e.g. "etsy", "notion"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    oauth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
