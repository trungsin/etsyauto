"""add template_id design_id to listings

Revision ID: 7e644acd83eb
Revises: a3d8008fe208
Create Date: 2026-05-06 15:54:04.782959

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e644acd83eb'
down_revision: Union[str, Sequence[str], None] = 'a3d8008fe208'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add template_id + design_id (nullable) to listings for sub-feature C.

    These link a listing back to the template and design used to create it via
    POST /listings/from-template. Nullable so legacy listings (v0.1.0+) remain valid.
    """
    op.add_column("listings", sa.Column("template_id", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("design_id", sa.Integer(), nullable=True))
    op.create_index("ix_listings_template_design", "listings", ["template_id", "design_id"])


def downgrade() -> None:
    op.drop_index("ix_listings_template_design", table_name="listings")
    op.drop_column("listings", "design_id")
    op.drop_column("listings", "template_id")
