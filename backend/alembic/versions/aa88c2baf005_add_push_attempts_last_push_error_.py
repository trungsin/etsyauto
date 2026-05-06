"""add_push_attempts_last_push_error_pushed_at_to_listings

Revision ID: aa88c2baf005
Revises: e7d4a402ca7b
Create Date: 2026-05-05 09:48:33.580476

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa88c2baf005'
down_revision: Union[str, Sequence[str], None] = 'e7d4a402ca7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add push tracking columns to listings table."""
    op.add_column("listings", sa.Column("push_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("listings", sa.Column("last_push_error", sa.Text(), nullable=True))
    op.add_column("listings", sa.Column("pushed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove push tracking columns from listings table."""
    op.drop_column("listings", "pushed_at")
    op.drop_column("listings", "last_push_error")
    op.drop_column("listings", "push_attempts")
