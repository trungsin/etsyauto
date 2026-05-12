"""add shop_id to api_credentials

Revision ID: c4f1a9d2e7b8
Revises: d17139d27fbb
Create Date: 2026-05-12 06:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a9d2e7b8'
down_revision: Union[str, Sequence[str], None] = 'd17139d27fbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_credentials") as batch_op:
        batch_op.add_column(sa.Column("shop_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_credentials") as batch_op:
        batch_op.drop_column("shop_id")
