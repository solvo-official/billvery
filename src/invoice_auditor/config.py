from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Vercel Functions reject a request body over 4.5 MB before it reaches the app, so uploads there
# are capped below that, leaving room for multipart framing.
VERCEL_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a `.env` file."""

    # env_ignore_empty: a variable that exists but is blank (common after pasting an example file
    # into a hosting dashboard) means "use the default", not "parse an empty string".
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    # --- Deployment -----------------------------------------------------------------------
    # Vercel sets VERCEL=1 in builds and functions; it switches the defaults below to the ones a
    # serverless function needs (no connection pool, documents in the database, smaller uploads).
    vercel: bool = False
    # Public origin of the deployment, used to build the Google OAuth redirect URI.
    public_url: str | None = None
    # Set automatically by Vercel, e.g. "invoice-auditor.vercel.app"; the fallback for public_url.
    vercel_project_production_url: str | None = None

    # --- Database -------------------------------------------------------------------------
    # Any PostgreSQL URL: the libpq form Neon prints (postgresql://...?sslmode=require) is
    # translated for asyncpg, including its TLS settings. See db.normalize_database_url.
    database_url: str | None = None
    # Neon's Vercel integration also exposes the direct (unpooled) endpoint. Migrations and other
    # DDL use it when set, because a transaction pooler is a poor fit for them.
    database_url_unpooled: str | None = None
    db_pool: Literal["auto", "null", "queue"] = Field(
        "auto", description="auto: no pool when running serverless, a pool otherwise."
    )
    db_ssl_verify: bool = Field(True, description="Verify the server certificate when TLS is requested.")
    db_pool_size: int = Field(10, ge=1)
    db_max_overflow: int = Field(10, ge=0)
    db_pool_timeout_s: float = Field(30.0, gt=0)
    db_pool_recycle_s: int = Field(1_800, ge=-1, description="Reconnect rather than reuse a connection older than this")
    db_connect_timeout_s: float = Field(15.0, gt=0, description="Includes waking a scale-to-zero database")
    db_statement_timeout_ms: int = Field(15_000, ge=0, description="0 disables the timeout")

    # --- Browser sessions and Google sign-in ------------------------------------------------
    # Signs the session cookie the console uses. Required for Google sign-in; without it only
    # API keys can authenticate. Generate with: python -c "import secrets;print(secrets.token_urlsafe(48))"
    jwt_secret: SecretStr | None = None
    session_ttl_hours: int = Field(12, ge=1, le=720)
    session_cookie_name: str = "ia_session"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    # --- Gemini extraction ----------------------------------------------------------------
    gemini_api_key: SecretStr | None = None
    # Gemini 1.5 Pro was shut down by Google in September 2025, so it cannot be the default.
    gemini_model: str = "gemini-3.1-pro-preview"
    gemini_timeout_s: float = Field(120.0, gt=0)
    gemini_max_attempts: int = Field(3, ge=1)

    # --- Uploaded documents ---------------------------------------------------------------
    # local: content-addressed files (<dir>/<organization>/<sha[:2]>/<sha256>), a relative path
    # resolving against the server's working directory. database: bytes in the `documents` table,
    # the only durable option on a serverless platform with a read-only file system.
    document_storage: Literal["auto", "local", "database"] = "auto"
    document_storage_dir: Path = Path("data/documents")
    max_upload_bytes: int = Field(18 * 1024 * 1024, gt=0, description="Gemini accepts ~20 MB inline")

    # --- Anomaly rules --------------------------------------------------------------------
    duplicate_window_days: int = Field(90, ge=1)
    similar_invoice_number_ratio: float = Field(0.8, gt=0, le=1)
    vendor_similarity_threshold: float = Field(0.6, gt=0, le=1)
    math_tolerance: Decimal = Decimal("0.05")
    price_spike_ratio: Decimal = Decimal("2.5")
    price_history_days: int = Field(365, ge=1)
    price_min_samples: int = Field(3, ge=1)
    tax_tolerance: Decimal = Decimal("0.05")
    auto_approve_confidence: Decimal = Decimal("90")
    critical_confidence: Decimal = Decimal("70")
    stale_invoice_days: int = Field(365, ge=1)
    future_date_grace_days: int = Field(1, ge=0)

    log_level: str = "INFO"

    @model_validator(mode="before")
    @classmethod
    def _strip_whitespace(cls, data: Any) -> Any:
        # Values pasted into a dashboard often carry a trailing space or line break (CRLF from a
        # Windows clipboard). None of these settings is meaningful with surrounding whitespace, and
        # a value that is only whitespace counts as unset.
        if not isinstance(data, dict):
            return data
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            cleaned[key] = value
        return cleaned

    @field_validator("jwt_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        # HS256 keys shorter than the digest add no security (RFC 7518 3.2).
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters; generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\"")
        return value

    def model_post_init(self, _context: object) -> None:
        # Serverless defaults, unless the deployment set them explicitly.
        if self.vercel and "max_upload_bytes" not in self.model_fields_set:
            self.max_upload_bytes = VERCEL_UPLOAD_LIMIT_BYTES

    @property
    def serverless(self) -> bool:
        return self.vercel

    @property
    def serverless_db_pool(self) -> bool:
        """True when every request should open and close its own connection."""
        return self.db_pool == "null" or (self.db_pool == "auto" and self.serverless)

    @property
    def documents_in_database(self) -> bool:
        return self.document_storage == "database" or (self.document_storage == "auto" and self.serverless)

    @property
    def migration_database_url(self) -> str | None:
        """Migrations prefer the direct endpoint: DDL and a transaction pooler mix badly.

        Without DATABASE_URL_UNPOOLED, a Neon pooler URL is turned into its direct twin: Neon's
        pooled host is the direct host with `-pooler` appended to the endpoint id.
        """
        if self.database_url_unpooled:
            return self.database_url_unpooled
        if not self.database_url:
            return None
        from sqlalchemy.engine import make_url

        url = make_url(self.database_url)
        if url.host and "-pooler." in url.host:
            return url.set(host=url.host.replace("-pooler.", ".", 1)).render_as_string(hide_password=False)
        return self.database_url

    @property
    def base_url(self) -> str | None:
        """Public origin of this deployment, without a trailing slash."""
        if self.public_url:
            return self.public_url.rstrip("/")
        if self.vercel_project_production_url:
            return f"https://{self.vercel_project_production_url.rstrip('/')}"
        return None

    @property
    def google_login_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.jwt_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
