"""add local draft fields to listings

Revision ID: 36665e3c284b
Revises: 8f11b5b558d5
Create Date: 2026-05-15 08:58:53.082781

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36665e3c284b'
down_revision: Union[str, Sequence[str], None] = '8f11b5b558d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add local_payload_json + deleted_at; make etsy_listing_id nullable.

    SQLite ALTER COLUMN limitations require batch mode to recreate the table when
    relaxing the NOT NULL constraint on etsy_listing_id.
    """
    with op.batch_alter_table("listings") as batch:
        batch.alter_column(
            "etsy_listing_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch.add_column(sa.Column("local_payload_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop new columns; revert etsy_listing_id to NOT NULL."""
    with op.batch_alter_table("listings") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("local_payload_json")
        batch.alter_column(
            "etsy_listing_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
