from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import read_session
from ..config import Settings
from ..enums import ApiScope, UserRole
from ..errors import Forbidden, Unauthorized
from ..models import ApiKey, Organization, User
from ..security import hash_api_key

# last_used_at is refreshed at most this often, so authentication is not a write per request.
LAST_USED_RESOLUTION = timedelta(minutes=5)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Everyone who can sign in may read and submit invoices; only the reviewer roles may decide.
SESSION_SCOPES = frozenset({ApiScope.INVOICES_READ.value, ApiScope.INVOICES_WRITE.value})
REVIEWER_SCOPES = SESSION_SCOPES | {ApiScope.ANOMALIES_RESOLVE.value}

bearer_scheme = HTTPBearer(auto_error=False, description="API key created with `invoice-auditor create-api-key`.")


@dataclass(frozen=True, slots=True)
class Principal:
    organization_id: UUID
    scopes: frozenset[str]
    api_key_id: UUID | None = None
    # Set when a person is signed in; their decisions are recorded against this user.
    user_id: UUID | None = None
    method: Literal["api_key", "session"] = "api_key"

    def acting_as(self, user_id: UUID) -> None:
        """Guard the *_by fields: a signed-in reviewer may only act as themselves."""
        if self.user_id is not None and user_id != self.user_id:
            raise Forbidden("You can only record decisions in your own name.")


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


async def _api_key_principal(session: AsyncSession, key: str) -> Principal:
    row = (
        await session.execute(
            select(ApiKey, Organization.is_active)
            .join(Organization, Organization.id == ApiKey.organization_id)
            .where(ApiKey.key_hash == hash_api_key(key))
        )
    ).first()
    if row is None:
        raise Unauthorized("Invalid API key.")
    api_key, organization_active = row
    now = datetime.now(UTC)
    if (
        api_key.revoked_at is not None
        or (api_key.expires_at is not None and api_key.expires_at <= now)
        or not organization_active
    ):
        raise Unauthorized("API key is revoked or expired, or its organization is inactive.")
    if api_key.last_used_at is None or now - api_key.last_used_at > LAST_USED_RESOLUTION:
        await session.execute(update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=now))
    await session.commit()  # close the auth transaction; the endpoint's work runs in its own
    return Principal(
        organization_id=api_key.organization_id,
        api_key_id=api_key.id,
        scopes=frozenset(api_key.permissions.get("scopes", [])),
        method="api_key",
    )


def _same_origin(request: Request, settings: Settings) -> bool:
    """True when an unsafe request came from this site's own pages.

    Cookies are SameSite=Lax, so a cross-site form post never carries one; this is the second
    lock. Browsers always send Origin on unsafe requests, so a missing one is refused too.
    """
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = urlsplit(origin).netloc.lower()
    allowed = {(request.headers.get("x-forwarded-host") or request.url.netloc).lower()}
    if settings.base_url:
        allowed.add(urlsplit(settings.base_url).netloc.lower())
    return host in allowed


async def _session_principal(request: Request, session: AsyncSession, settings: Settings) -> Principal:
    claims = read_session(settings, request.cookies.get(settings.session_cookie_name))
    if claims is None:
        raise Unauthorized("Your session has expired. Sign in again.")
    if request.method not in SAFE_METHODS and not _same_origin(request, settings):
        raise Forbidden("This request did not come from the console.")
    row = (
        await session.execute(
            select(User, Organization.is_active)
            .join(Organization, Organization.id == User.organization_id)
            .where(User.id == claims.user_id, User.organization_id == claims.organization_id)
        )
    ).first()
    if row is None:
        raise Unauthorized("Your account no longer exists.")
    user, organization_active = row
    if not user.is_active or not organization_active:
        raise Unauthorized("Your account is no longer active.")
    role = UserRole(user.role)
    return Principal(
        organization_id=user.organization_id,
        scopes=frozenset(REVIEWER_SCOPES if role.can_resolve_anomalies else SESSION_SCOPES),
        user_id=user.id,
        method="session",
    )


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> Principal:
    """Authenticate the caller: an API key for integrations, or the console's session cookie."""
    if credentials is not None:
        return await _api_key_principal(session, credentials.credentials)
    if request.cookies.get(settings.session_cookie_name):
        return await _session_principal(request, session, settings)
    raise Unauthorized("Missing API key. Send 'Authorization: Bearer <key>'.")


def require_scope(scope: ApiScope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if scope.value not in principal.scopes:
            if principal.method == "session":
                raise Forbidden(f"Your role cannot {scope.value.replace(':', ' ')}.")
            raise Forbidden(f"This API key lacks the '{scope.value}' scope.")
        return principal

    return dependency
