"""Serverless deployment details: Neon URLs, pooling, and the limits a function imposes."""

import ssl

import pytest

from invoice_auditor.config import VERCEL_UPLOAD_LIMIT_BYTES, Settings
from invoice_auditor.db import connection_settings, create_engine, is_pooled, normalize_database_url

NEON_POOLED = (
    "postgresql://auditor:secret@ep-quiet-bird-12345-pooler.us-east-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)
NEON_DIRECT = "postgresql://auditor:secret@ep-quiet-bird-12345.us-east-1.aws.neon.tech/neondb?sslmode=verify-full"


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_neon_url_is_translated_for_asyncpg():
    url, connect_args = normalize_database_url(NEON_POOLED)
    assert url.drivername == "postgresql+asyncpg"
    assert url.host.endswith("neon.tech") and url.database == "neondb"
    # libpq keywords asyncpg cannot take are gone from the URL...
    assert dict(url.query) == {}
    # ...and sslmode became a verifying TLS context.
    assert isinstance(connect_args["ssl"], ssl.SSLContext)
    assert connect_args["ssl"].verify_mode is ssl.CERT_REQUIRED


@pytest.mark.parametrize(
    ("sslmode", "expected"),
    [("disable", False), ("prefer", "prefer"), ("allow", "prefer")],
)
def test_ssl_modes(sslmode, expected):
    _, connect_args = normalize_database_url(f"postgresql://u:p@host/db?sslmode={sslmode}")
    assert connect_args["ssl"] == expected


def test_ssl_verification_can_be_relaxed():
    _, connect_args = normalize_database_url(NEON_DIRECT, verify_ssl=False)
    assert connect_args["ssl"] == "require"  # encrypted, certificate unchecked


def test_unknown_parameters_are_dropped_rather_than_passed_to_asyncpg():
    url, _ = normalize_database_url("postgresql://u:p@host/db?target_session_attrs=read-write&options=-csearch_path%3Dx")
    assert dict(url.query) == {}


def test_plain_and_driver_urls_are_accepted():
    assert normalize_database_url("postgres://u:p@h/db")[0].drivername == "postgresql+asyncpg"
    assert normalize_database_url("postgresql+asyncpg://u:p@h/db")[0].drivername == "postgresql+asyncpg"
    with pytest.raises(ValueError):
        normalize_database_url("mysql://u:p@h/db")


def test_pooler_endpoints_are_recognised():
    assert is_pooled(normalize_database_url(NEON_POOLED)[0], NEON_POOLED)
    assert not is_pooled(normalize_database_url(NEON_DIRECT)[0], NEON_DIRECT)
    raw = "postgresql://u:p@host/db?pgbouncer=true"
    assert is_pooled(normalize_database_url(raw)[0], raw)


def test_serverless_engine_opens_no_pool():
    engine = create_engine(settings(database_url=NEON_POOLED, vercel=True))
    assert type(engine.pool).__name__ == "NullPool"
    _, connect_args, serverless = connection_settings(settings(database_url=NEON_POOLED, vercel=True))
    assert serverless is True
    # Behind a transaction pooler nothing may be cached between statements.
    assert connect_args["statement_cache_size"] == 0
    assert connect_args["prepared_statement_cache_size"] == 0
    assert connect_args["prepared_statement_name_func"]() != connect_args["prepared_statement_name_func"]()
    # A pooler rejects startup parameters it does not track, so the ceiling moves client-side.
    assert "statement_timeout" not in connect_args["server_settings"]
    assert connect_args["command_timeout"] == 15.0


def test_server_engine_keeps_a_pool_and_a_server_side_statement_timeout():
    local = settings(database_url="postgresql+asyncpg://u:p@localhost/db")
    engine = create_engine(local)
    assert "QueuePool" in type(engine.pool).__name__
    _, connect_args, serverless = connection_settings(local)
    assert serverless is False
    assert connect_args["server_settings"]["statement_timeout"] == "15000"
    assert "prepared_statement_name_func" not in connect_args


def test_vercel_defaults_fit_the_platform():
    on_vercel = settings(database_url=NEON_POOLED, vercel=True)
    assert on_vercel.serverless_db_pool is True
    assert on_vercel.documents_in_database is True
    # Vercel refuses a request body over 4.5 MB before it reaches the app.
    assert on_vercel.max_upload_bytes == VERCEL_UPLOAD_LIMIT_BYTES

    local = settings(database_url="postgresql+asyncpg://u:p@localhost/db")
    assert (local.serverless_db_pool, local.documents_in_database) == (False, False)
    assert local.max_upload_bytes == 18 * 1024 * 1024

    explicit = settings(database_url=NEON_POOLED, vercel=True, max_upload_bytes=1024, db_pool="queue", document_storage="local")
    assert (explicit.max_upload_bytes, explicit.serverless_db_pool, explicit.documents_in_database) == (1024, False, False)


def test_migrations_prefer_the_direct_endpoint():
    both = settings(database_url=NEON_POOLED, database_url_unpooled=NEON_DIRECT)
    assert both.migration_database_url == NEON_DIRECT
    # Only the pooled URL configured: its direct twin is derived.
    derived = settings(database_url=NEON_POOLED).migration_database_url
    assert "-pooler" not in derived and "ep-quiet-bird-12345.us-east-1.aws.neon.tech" in derived
    assert "secret" in derived and "sslmode=require" in derived  # credentials and TLS kept
    plain = "postgresql+asyncpg://u:p@localhost/db"
    assert settings(database_url=plain).migration_database_url == plain


def test_vercel_entrypoint_reports_startup_failures_without_secrets(monkeypatch):
    """A misconfigured deployment answers 503 with the reason instead of crashing opaquely."""
    import asyncio
    import importlib.util
    from pathlib import Path

    import httpx

    from invoice_auditor.config import get_settings

    def load():
        get_settings.cache_clear()
        spec = importlib.util.spec_from_file_location("vercel_index", Path(__file__).parent.parent / "api" / "index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    async def healthz(module):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="https://x.vercel.app") as client:
            return await client.get("/healthz")

    for key in ("DATABASE_URL", "JWT_SECRET", "DATABASE_URL_UNPOOLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # no .env here
    try:
        monkeypatch.setenv("VERCEL", "1")
        response = asyncio.run(healthz(load()))
        assert response.status_code == 503
        assert response.json()["error"] == {"code": "startup_failed", "message": "DATABASE_URL is not set", "details": None}

        monkeypatch.setenv("DATABASE_URL", "postgresql://neondb_owner:hunter2-password@ep-x-pooler.aws.neon.tech/db")
        monkeypatch.setenv("JWT_SECRET", "short-but-secret-value")
        body = asyncio.run(healthz(load())).text
        assert "JWT_SECRET" in body and "at least 32" in body
        assert "short-but-secret-value" not in body and "hunter2-password" not in body

        monkeypatch.setenv("JWT_SECRET", "x" * 40)
        monkeypatch.setenv("DATABASE_URL", '"postgresql://neondb_owner:hunter2-password@ep-x-pooler.aws.neon.tech/db"')
        body = asyncio.run(healthz(load())).text
        assert "could not be parsed" in body and "hunter2-password" not in body
    finally:
        get_settings.cache_clear()


def test_public_base_url_falls_back_to_the_vercel_domain():
    assert settings(public_url="https://audit.example.com/").base_url == "https://audit.example.com"
    assert settings(vercel_project_production_url="ia.vercel.app").base_url == "https://ia.vercel.app"
    assert settings().base_url is None


def test_jwt_secret_must_be_long_enough():
    with pytest.raises(ValueError, match="at least 32"):
        settings(jwt_secret="too-short")


# --- Documents in the database (the serverless storage backend) --------------------------------


@pytest.mark.integration
async def test_uploads_are_stored_in_and_served_from_the_database(db, settings, tenant, monkeypatch):
    """With a read-only file system the bytes live in `documents`, and /document still serves them."""
    import httpx
    from fastapi import FastAPI  # noqa: F401  (imported for the type of create_app's result)

    from invoice_auditor.main import create_app
    from invoice_auditor.storage import DatabaseDocumentStore

    from .test_api_live import PDF, FakeExtractor, extraction, upload

    settings.document_storage = "database"
    app = create_app(settings)
    assert isinstance(app.state.documents, DatabaseDocumentStore)
    app.state.extractor = FakeExtractor(extraction())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            audit = (await upload(client, tenant))[-1]["audit"]
            assert audit["document"]["available"] is True
            assert audit["document"]["storage_url"].startswith("db://")

            original = await client.get(f"/api/v1/invoices/{audit['invoice_id']}/document", headers=tenant.headers)
            assert original.status_code == 200
            assert original.content == PDF
            assert original.headers["content-type"] == "application/pdf"
            assert original.headers["content-disposition"].startswith("inline")

            # Nothing was written to disk.
            assert not settings.document_storage_dir.exists()


@pytest.mark.integration
async def test_another_tenant_cannot_read_a_stored_document(db, settings, tenant):
    from uuid import uuid4

    from invoice_auditor.storage import DatabaseDocumentStore

    store = DatabaseDocumentStore()
    sha = "ab" * 32
    url = await store.save(db, tenant.organization_id, sha, b"%PDF-1.4 secret")
    assert await store.fetch(db, url, tenant.organization_id) == b"%PDF-1.4 secret"
    assert await store.fetch(db, url, uuid4()) is None
    assert await store.fetch(db, "local://x/y", tenant.organization_id) is None
    # Saving the same bytes twice is a no-op, exactly like the content-addressed file store.
    assert await store.save(db, tenant.organization_id, sha, b"%PDF-1.4 secret") == url
