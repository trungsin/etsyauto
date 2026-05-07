"""unique constraint listings template_id design_id

Revision ID: 415233eecbd4
Revises: 7e644acd83eb
Create Date: 2026-05-07 08:05:11.476580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '415233eecbd4'
down_revision: Union[str, Sequence[str], None] = '7e644acd83eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add UNIQUE index on (template_id, design_id) to prevent duplicate Etsy
    drafts from concurrent submits. Replaces the non-unique index added in
    7e644acd83eb so we don't keep two indexes on the same columns.

    NULL semantics: SQLite + Postgres both treat NULLs as distinct, so legacy
    listings (template_id=NULL, design_id=NULL from v0.1.0 ingest flow)
    coexist freely. The constraint only fires when both columns are non-NULL.
    """
    op.drop_index("ix_listings_template_design", table_name="listings")
    op.create_index(
        "ux_listings_template_design",
        "listings",
        ["template_id", "design_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_listings_template_design", table_name="listings")
    op.create_index(
        "ix_listings_template_design",
        "listings",
        ["template_id", "design_id"],
    )
