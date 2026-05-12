"""add design_id to ideas

Adds a nullable FK column `design_id` on the `ideas` table pointing at `designs.id`.
ON DELETE SET NULL — deleting a design preserves the idea row.

Revision ID: f3a1b2c4d5e6
Revises: c4f1a9d2e7b8
Create Date: 2026-05-12 11:32:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1b2c4d5e6"
down_revision: Union[str, Sequence[str], None] = "c4f1a9d2e7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add design_id FK column to ideas table."""
    with op.batch_alter_table("ideas") as batch:
        batch.add_column(sa.Column("design_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_ideas_design_id",
            "designs",
            ["design_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Drop design_id FK and column from ideas table."""
    with op.batch_alter_table("ideas") as batch:
        batch.drop_constraint("fk_ideas_design_id", type_="foreignkey")
        batch.drop_column("design_id")
