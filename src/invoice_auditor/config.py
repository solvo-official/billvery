import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource

# Vercel Functions reject a request body over 4.5 MB before it reaches the app, so uploads there
# are capped below that, leaving room for multipart framing.
VERCEL_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024

_NUMBERED_GEMINI_KEY = re.compile(r"GEMINI_API_KEY_(\d{1,3})", re.IGNORECASE)
_KEY_LIST_SEPARATORS = re.compile(r"[\s,;]+")


class _NumberedGeminiKeys(PydanticBaseSettingsSource):
    """GEMINI_API_KEY_1, GEMINI_API_KEY_2, ... in number order, from the environment and the .env file.

    Settings only reads variables it declares a field for, so numbered names need their own source.
    """

    def __init__(self, settings_cls: type[BaseSettings], dotenv: PydanticBaseSettingsSource) -> None:
        super().__init__(settings_cls)
        self._dotenv = dotenv

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        found: dict[int, str] = {}
        if isinstance(self._dotenv, DotEnvSettingsSource):
            env_files = self._dotenv.env_file
            paths = [] if env_files is None else [env_files] if isinstance(env_files, (str, os.PathLike)) else list(env_files)
            for path in paths:
                if Path(path).expanduser().is_file():
                    found.update(_numbered(dotenv_values(Path(path).expanduser(), encoding=self._dotenv.env_file_encoding)))
        found.update(_numbered(os.environ))  # the environment wins over the file, as for every other setting
        keys = [found[number] for number in sorted(found)]
        return {"gemini_numbered_api_keys": keys} if keys else {}


def _numbered(variables: Any) -> dict[int, str]:
    numbered: dict[int, str] = {}
    for name, value in variables.items():
        match = _NUMBERED_GEMINI_KEY.fullmatch(name)
        if match and value and value.strip():
            numbered[int(match.group(1))] = value.strip()
    return numbered


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

    # --- Workspaces --------------------------------------------------------------------------
    # A Google account that signs in without belonging to any workspace gets its own. Leave
    # SIGNUP_ALLOWLIST empty to let any Google account do that, or list who may, comma separated:
    # full addresses (name@gmail.com) and whole domains (@company.com).
    signup_allowlist: str | None = None
    workspace_default_currency: str = Field("USD", pattern=r"^[A-Za-z]{3}$")
    max_workspaces_per_account: int = Field(5, ge=1, le=100)

    # --- Gemini extraction ----------------------------------------------------------------
    # One key, or a pool that requests rotate through: GEMINI_API_KEYS (separated by commas, spaces
    # or new lines) and/or GEMINI_API_KEY_1, GEMINI_API_KEY_2, ... Every distinct key is used.
    # Google applies rate limits per Cloud project, so keys from the same project share one quota.
    gemini_api_key: SecretStr | None = None
    gemini_api_keys: SecretStr | None = None
    gemini_numbered_api_keys: list[SecretStr] = Field(default_factory=list, exclude=True)
    # Gemini 1.5 Pro was shut down by Google in September 2025, so it cannot be the default.
    gemini_model: str = "gemini-3.1-pro-preview"
    gemini_timeout_s: float = Field(120.0, gt=0)
    gemini_max_attempts: int = Field(3, ge=1, description="Per key, for timeouts and 5xx; a 429 moves to the next key instead")
    gemini_rate_limit_cooldown_s: float = Field(
        60.0, gt=0, description="How long a key rests after a rate limit when Google doesn't say how long"
    )
    gemini_max_throttle_wait_s: float = Field(
        30.0, ge=0, description="How long an upload waits for a resting key before reporting the batch as throttled"
    )

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, dotenv_settings, _NumberedGeminiKeys(settings_cls, dotenv_settings), file_secret_settings

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

    def signup_allowed(self, email: str) -> bool:
        """May this Google account create a workspace of its own?"""
        if not self.signup_allowlist:
            return True
        email = email.strip().lower()
        domain = "@" + email.rpartition("@")[2]
        entries = {entry.strip().lower() for entry in self.signup_allowlist.split(",") if entry.strip()}
        return email in entries or domain in entries

    @property
    def gemini_key_pool(self) -> list[str]:
        """Every distinct Gemini API key, in order: GEMINI_API_KEYS, GEMINI_API_KEY, then the numbered ones."""
        keys: list[str] = []
        if self.gemini_api_keys is not None:
            keys += _KEY_LIST_SEPARATORS.split(self.gemini_api_keys.get_secret_value())
        if self.gemini_api_key is not None:
            keys.append(self.gemini_api_key.get_secret_value())
        keys += [key.get_secret_value() for key in self.gemini_numbered_api_keys]
        return list(dict.fromkeys(key.strip() for key in keys if key.strip()))

    @property
    def google_login_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.jwt_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
