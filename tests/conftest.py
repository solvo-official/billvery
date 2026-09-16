"""Integration tests need a PostgreSQL 15+ server. Point TEST_DATABASE_URL at a database the
test user can connect to and create databases from, e.g.

    TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/postgres

Each test session creates a throwaway database, migrates it with Alembic and drops it at the end.
Without TEST_DATABASE_URL the integration tests are skipped.
"""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator

import asyncpg
import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url

from invoice_auditor.cli import alembic_config
from invoice_auditor.config import Settings
from invoice_auditor.db import Database
from invoice_auditor.main import create_app

from .factories import Tenant, make_tenant

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if ADMIN_URL:
        return
    skip = pytest.mark.skip(reason="set TEST_DATABASE_URL to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def _url(database: str, driver: str) -> str:
    return make_url(ADMIN_URL).set(drivername=driver, database=database).render_as_string(hide_password=False)


async def _admin(sql: str) -> None:
    connection = await asyncpg.connect(_url(make_url(ADMIN_URL).database or "postgres", "postgresql"))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


def create_database() -> tuple[str, str]:
    """Create an empty database; returns (name, asyncpg SQLAlchemy URL)."""
    name = f"invoice_auditor_test_{secrets.token_hex(4)}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    return name, _url(name, "postgresql+asyncpg")


def drop_database(name: str) -> None:
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if not ADMIN_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    name, url = create_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(name)


@pytest.fixture
def settings(database_url: str, tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        db_pool_size=5,
        log_level="WARNING",
        document_storage_dir=tmp_path / "documents",
        jwt_secret="test-secret-that-is-long-enough-to-sign-with",
    )


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings)
    async with database.engine.begin() as connection:
        # TRUNCATE does not fire the row-level guard triggers, so the audit log can be reset.
        await connection.execute(text("TRUNCATE organizations, tax_rate_limits RESTART IDENTITY CASCADE"))
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
async def app(settings: Settings, db: Database) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
async def tenant(db: Database) -> Tenant:
    return await make_tenant(db)
