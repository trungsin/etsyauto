"""add color_base_images_json to templates

Revision ID: a3d8008fe208
Revises: 33596c423a0e
Create Date: 2026-05-06 15:33:25.305226

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d8008fe208'
down_revision: Union[str, Sequence[str], None] = '33596c423a0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add color_base_images_json column to templates table.

    JSON map: {"White": "https://r2.../templates/abc-white.png", ...}
    Keys must match colors listed in variation_options_json.colors.
    """
    op.add_column(
        "templates",
        sa.Column(
            "color_base_images_json",
            sa.String(length=2000),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    """Drop color_base_images_json column."""
    op.drop_column("templates", "color_base_images_json")
