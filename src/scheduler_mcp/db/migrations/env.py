"""Alembic env — uses cloud_dog_db migration runner conventions.

Reads the SQLAlchemy URL from cloud_dog_config (db.url). The metadata is the
union of all scheduler-mcp ORM model tables registered via Base.metadata.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Import models so their tables are registered on metadata
from scheduler_mcp import config as scheduler_config
from scheduler_mcp.db.models import metadata as target_metadata  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_url() -> str:
    """Prefer the alembic.ini-supplied URL; fall back to platform config."""
    cfg_url = config.get_main_option("sqlalchemy.url")
    if cfg_url:
        return cfg_url
    return scheduler_config.require("db.url")


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
