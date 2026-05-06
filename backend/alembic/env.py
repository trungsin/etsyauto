"""Alembic env.py — wires SQLAlchemy metadata and DATABASE_URL from app config."""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make sure `app` package is importable when running alembic from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# Import all models so their tables are registered on Base.metadata
from app.models.listing import Listing  # noqa: E402, F401
from app.models.title_variant import TitleVariant  # noqa: E402, F401
from app.models.mockup_variant import MockupVariant  # noqa: E402, F401
from app.models.job import Job  # noqa: E402, F401
from app.models.api_credential import ApiCredential  # noqa: E402, F401
from app.models.template import Template  # noqa: E402, F401
from app.models.template_variation import TemplateVariation  # noqa: E402, F401
from app.models.design import Design  # noqa: E402, F401

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Override sqlalchemy.url from app settings so .env is the single source of truth
alembic_config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emit SQL to stdout)."""
    url = alembic_config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
