"""Initial schema: listings, title_variants, mockup_variants, jobs, api_credentials.

Revision ID: 0001
Revises:
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("etsy_listing_id", sa.String(64), nullable=False, unique=True),
        sa.Column("original_title", sa.String(512), nullable=False),
        sa.Column("original_desc", sa.Text(), nullable=True),
        sa.Column("original_tags", sa.Text(), nullable=True),
        sa.Column("original_images", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_listings_etsy_listing_id", "listings", ["etsy_listing_id"])

    op.create_table(
        "title_variants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(512), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("ai_rationale", sa.Text(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.create_index("ix_title_variants_listing_id", "title_variants", ["listing_id"])

    op.create_table(
        "mockup_variants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_image_url", sa.String(1024), nullable=True),
        sa.Column("removed_bg_path", sa.String(1024), nullable=True),
        sa.Column("final_image_path", sa.String(1024), nullable=True),
        sa.Column("scene_prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.create_index("ix_mockup_variants_listing_id", "mockup_variants", ["listing_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("listing_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_log", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_listing_id", "jobs", ["listing_id"])

    op.create_table(
        "api_credentials",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("oauth_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_credentials")
    op.drop_table("jobs")
    op.drop_table("mockup_variants")
    op.drop_table("title_variants")
    op.drop_table("listings")
