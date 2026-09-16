import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from invoice_auditor.config import get_settings
from invoice_auditor.db import normalize_database_url
from invoice_auditor.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """The URL to migrate: the direct endpoint when one is configured, never the pooler."""
    url = config.attributes.get("database_url") or get_settings().migration_database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _configure_and_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    url, connect_args = normalize_database_url(_database_url())
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_configure_and_run)
    finally:
        await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=normalize_database_url(_database_url())[0].render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
elif (connection := config.attributes.get("connection")) is not None:
    # Programmatic use (tests, admin tooling) from code that already holds a sync connection.
    _configure_and_run(connection)
else:
    asyncio.run(_run_online())
