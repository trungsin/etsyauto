"""Listing model — mirrors an Etsy listing with optimization state."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # v0.10: nullable for local-draft listings that have not been uploaded to Etsy yet.
    # SQLite/Postgres unique allows multiple NULLs per SQL standard.
    etsy_listing_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    original_title: Mapped[str] = mapped_column(String(512), nullable=False)
    original_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialized list of strings
    original_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-serialized list of image URLs
    original_images: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status domain (v0.10+):
    #   new        — local draft, no etsy_listing_id, fully editable
    #   uploading  — transient lock during upload_to_etsy
    #   created    — live on Etsy, edits write-through
    #   syncing    — transient lock during sync from Etsy
    #   failed     — last operation errored, see last_push_error
    #   deleted    — soft-deleted (Etsy state=inactive); see deleted_at
    # Legacy values (pending/review/approved/pushed) retained for backward-compat.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # Notion page ID — set after sync_to_notion creates the review page
    notion_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Push tracking — populated by etsy_uploader worker
    push_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_push_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Sub-feature C: link back to template/design when listing created via /listings/from-template
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    design_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # v0.10: local source-of-truth payload for drafts and editable fields.
    # JSON-serialized {title, description, tags, enabled_combos, zone_designs}.
    local_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v0.10: soft-delete marker (Etsy DELETE leaves Etsy listing in 'inactive' state).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
