"""Engine construction, including the parts that differ between a long-running server and a
serverless function talking to a managed Postgres such as Neon.

Neon (and psql, Heroku, Railway, ...) hand out libpq URLs like

    postgresql://user:pass@ep-x-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require

which asyncpg cannot consume as-is: it has no `sslmode`, and SQLAlchemy forwards unknown query
parameters straight to `asyncpg.connect()`, where they raise TypeError. `normalize_database_url`
translates such a URL into an asyncpg URL plus connect arguments, so the value Neon prints can be
pasted into DATABASE_URL unchanged.
"""

import logging
import ssl
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import Settings

logger = logging.getLogger(__name__)

# Drivers that mean "connect to PostgreSQL"; this project always uses asyncpg.
_POSTGRES_DRIVERS = frozenset({"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+asyncpg"})

# libpq keywords asyncpg does not accept as connect() arguments and that have no asyncpg
# equivalent. (sslmode, sslrootcert, connect_timeout and application_name are translated below.)
_DROPPED = frozenset({"channel_binding", "target_session_attrs", "options", "gssencmode", "sslcert", "sslkey", "pgbouncer"})

# asyncpg/SQLAlchemy connect arguments a URL may carry through untouched.
_PASSTHROUGH = frozenset({"statement_cache_size", "prepared_statement_cache_size"})

_ENCRYPTED_MODES = frozenset({"require", "verify-ca", "verify-full"})


def _ssl_argument(mode: str, *, root_cert: str | None, verify: bool) -> Any:
    """asyncpg's `ssl` connect argument for a libpq sslmode."""
    if mode in ("disable", "false"):
        return False
    if mode in ("allow", "prefer"):
        return "prefer"
    if mode not in _ENCRYPTED_MODES and mode not in ("true", "require"):
        raise ValueError(f"unsupported sslmode {mode!r}")
    if not verify and root_cert is None:
        # libpq's own `require`: encrypt, but do not check the certificate.
        return "require"
    # Stricter than libpq's `require`, and what `verify-full` asks for: check the chain and the
    # host name. Managed providers (Neon included) serve publicly trusted certificates.
    return ssl.create_default_context(cafile=root_cert)


def normalize_database_url(raw: str, *, verify_ssl: bool = True) -> tuple[URL, dict[str, Any]]:
    """Return an asyncpg SQLAlchemy URL and the connect arguments its query string implied."""
    url = make_url(raw)
    if url.drivername not in _POSTGRES_DRIVERS:
        raise ValueError(f"DATABASE_URL must be a PostgreSQL URL, got driver {url.drivername!r}")
    query: dict[str, Any] = {key: value for key, value in url.query.items()}
    connect_args: dict[str, Any] = {}

    sslmode = str(query.pop("sslmode", query.pop("ssl", "")) or "").lower()
    root_cert = query.pop("sslrootcert", None)
    if sslmode or root_cert:
        connect_args["ssl"] = _ssl_argument(sslmode or "require", root_cert=root_cert, verify=verify_ssl)
    if (timeout := query.pop("connect_timeout", None)) is not None:
        connect_args["timeout"] = float(timeout)
    if (application_name := query.pop("application_name", None)) is not None:
        connect_args["server_settings"] = {"application_name": str(application_name)}

    for key in list(query):
        if key in _DROPPED:
            query.pop(key)
        elif key not in _PASSTHROUGH:
            logger.warning("ignoring unsupported DATABASE_URL parameter %r", key)
            query.pop(key)

    return url.set(drivername="postgresql+asyncpg", query=query), connect_args


def is_pooled(url: URL, raw: str = "") -> bool:
    """True for a connection-pooler endpoint: Neon's `-pooler` host, or an explicit pgbouncer flag."""
    host = (url.host or "").lower()
    return "-pooler." in host or host.startswith("pgbouncer") or "pgbouncer=true" in raw.lower()


def connection_settings(settings: Settings, url: str | None = None) -> tuple[URL, dict[str, Any], bool]:
    """Everything the engine needs: the asyncpg URL, its connect arguments, and whether to pool.

    Kept separate from `create_engine` so the choices can be inspected (and tested) without
    building an engine.
    """
    raw = url or settings.database_url
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    parsed, connect_args = normalize_database_url(raw, verify_ssl=settings.db_ssl_verify)
    pooled = is_pooled(parsed, raw)

    server_settings: dict[str, str] = {"application_name": "invoice-auditor", **connect_args.pop("server_settings", {})}
    if settings.db_statement_timeout_ms and not pooled:
        # A connection pooler in transaction mode rejects startup parameters it does not track,
        # so behind one the same ceiling is enforced client-side with command_timeout instead.
        server_settings["statement_timeout"] = str(settings.db_statement_timeout_ms)
    elif settings.db_statement_timeout_ms:
        connect_args["command_timeout"] = settings.db_statement_timeout_ms / 1000
    connect_args["server_settings"] = server_settings
    connect_args.setdefault("timeout", settings.db_connect_timeout_s)

    if pooled:
        # PgBouncer in transaction mode hands successive transactions to different server
        # connections, so nothing may be cached across them and every prepared statement needs a
        # name of its own.
        connect_args.setdefault("statement_cache_size", 0)
        connect_args.setdefault("prepared_statement_cache_size", 0)
        connect_args.setdefault("prepared_statement_name_func", lambda: f"__asyncpg_{uuid4()}__")

    return parsed, connect_args, settings.serverless_db_pool


def create_engine(settings: Settings, url: str | None = None) -> AsyncEngine:
    """The engine for this process: pooled for a server, per-request connections in a function."""
    parsed, connect_args, serverless = connection_settings(settings, url)
    if serverless:
        # One connection per request, closed at the end: a function instance can be frozen or
        # discarded at any moment, and an idle pooled connection would hold a Neon slot open.
        return create_async_engine(parsed, poolclass=NullPool, connect_args=connect_args)
    return create_async_engine(
        parsed,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        pool_recycle=settings.db_pool_recycle_s,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


class Database:
    """Owns the engine and session factory for one application instance."""

    def __init__(self, settings: Settings) -> None:
        self.engine = create_engine(settings)
        # expire_on_commit=False: objects stay readable after commit. Under asyncio an expired
        # attribute would trigger an implicit (and illegal) lazy load.
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def dispose(self) -> None:
        await self.engine.dispose()
