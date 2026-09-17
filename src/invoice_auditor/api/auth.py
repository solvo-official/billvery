"""Sign-in endpoints for the browser console.

The flow is: the console sends the person to `/api/v1/auth/google/login`, Google returns them to
`/api/v1/auth/google/callback`, and this service sets an HttpOnly session cookie and redirects
back into the app. Integrations keep using API keys and never touch these routes.

Every Google account works in a workspace of its own: signing in without any membership creates
one, and people invited elsewhere can switch between the workspaces they belong to.
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..auth import SignInError, exchange_code, issue_session, read_state, safe_next_path, start_login
from ..errors import AppError, Forbidden, NotFound, ServiceUnavailable
from ..models import Organization, User
from ..schemas.api import (
    CreateWorkspaceRequest,
    ErrorResponse,
    OrganizationOut,
    SessionOut,
    SwitchWorkspaceRequest,
    UserOut,
    WorkspaceOut,
)
from ..services.workspaces import active_memberships, create_workspace, lock_account
from .deps import Principal, SessionDep, SettingsDep, get_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

STATE_COOKIE = "ia_oauth"
STATE_COOKIE_PATH = "/api/v1/auth"
CALLBACK_PATH = "/api/v1/auth/google/callback"


def _base_url(request: Request, settings: SettingsDep) -> str:
    """This deployment's public origin, as the browser reached it."""
    if settings.base_url:
        return settings.base_url
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _secure(base_url: str) -> bool:
    # A Secure cookie is dropped by browsers over plain http, which local development uses.
    return base_url.startswith("https://")


def _fail(base_url: str, reason: str) -> RedirectResponse:
    """Back to the console with a reason it can explain."""
    response = RedirectResponse(f"{base_url}/?{urlencode({'auth_error': reason})}", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response


@router.get(
    "/session",
    response_model=SessionOut,
    responses={401: {"model": ErrorResponse, "description": "Not signed in and no API key was sent."}},
)
async def read_current_session(
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
) -> SessionOut:
    """Who the caller is. The console calls this first and shows the sign-in page on a 401."""
    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFound("Organization not found.")
    user = await session.get(User, principal.user_id) if principal.user_id else None
    return SessionOut(
        method=principal.method,
        user=UserOut.from_model(user) if user else None,
        organization=OrganizationOut.from_model(organization),
    )


@router.get("/google/login", response_class=RedirectResponse, responses={503: {"model": ErrorResponse}})
async def google_login(
    request: Request,
    settings: SettingsDep,
    next: Annotated[str, Query(max_length=512, description="Path in the console to return to.")] = "/",
) -> RedirectResponse:
    """Start Google sign-in: redirects to Google's consent screen."""
    base_url = _base_url(request, settings)
    authorization_url, state_token = start_login(
        settings, redirect_uri=f"{base_url}{CALLBACK_PATH}", next_path=safe_next_path(next)
    )
    response = RedirectResponse(authorization_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(
        STATE_COOKIE,
        state_token,
        max_age=600,
        httponly=True,
        secure=_secure(base_url),
        samesite="lax",
        path=STATE_COOKIE_PATH,
    )
    return response


@router.get("/google/callback", response_class=RedirectResponse, include_in_schema=False)
async def google_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    """Finish Google sign-in and set the session cookie."""
    base_url = _base_url(request, settings)
    if error or not code or not state:
        return _fail(base_url, "cancelled" if error == "access_denied" else "no_code")

    stored = read_state(settings, request.cookies.get(STATE_COOKIE))
    if stored is None:
        return _fail(base_url, "state_expired")
    if not secrets.compare_digest(str(stored.get("state", "")), state):
        logger.warning("OAuth state mismatch from %s", request.client.host if request.client else "unknown")
        return _fail(base_url, "state_mismatch")

    try:
        identity = await exchange_code(
            settings, code=code, verifier=str(stored["verifier"]), redirect_uri=str(stored["redirect_uri"])
        )
    except SignInError as exc:
        return _fail(base_url, exc.reason)
    except ServiceUnavailable:
        return _fail(base_url, "not_configured")

    # Back into the workspace used most recently, or a new private one for a first sign-in.
    await lock_account(session, identity["email"])
    memberships = await active_memberships(session, identity["email"])
    if memberships:
        user = memberships[0]
    else:
        try:
            user = await create_workspace(session, settings, email=identity["email"], display_name=identity.get("name"))
        except Forbidden:
            logger.info("sign-in refused for %s: not on SIGNUP_ALLOWLIST and not invited anywhere", identity["email"])
            return _fail(base_url, "signup_closed")
        except AppError:
            return _fail(base_url, "workspace_limit")
        logger.info("created a workspace for %s", identity["email"])
    if not user.full_name and identity.get("name"):
        user.full_name = str(identity["name"])[:150]
    user.last_login_at = datetime.now(UTC)
    await session.commit()

    token, _expires_at = issue_session(settings, user)
    response = RedirectResponse(f"{base_url}{safe_next_path(str(stored.get('next')))}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=_secure(base_url),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    logger.info("signed in %s (%s)", user.email, user.id)
    return response


# --- Workspaces ------------------------------------------------------------------------------


def _set_session_cookie(response: Response, request: Request, settings: SettingsDep, user: User) -> None:
    token, _expires_at = issue_session(settings, user)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=_secure(_base_url(request, settings)),
        samesite="lax",
        path="/",
    )


async def _signed_in_user(principal: Principal, session: SessionDep) -> User:
    if principal.user_id is None:
        raise Forbidden("Sign in with Google to use workspaces.")
    user = await session.get(User, principal.user_id)
    if user is None:
        raise NotFound("Your account no longer exists.")
    return user


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(principal: Annotated[Principal, Depends(get_principal)], session: SessionDep) -> list[WorkspaceOut]:
    """The workspaces the signed-in Google account belongs to, most recently used first."""
    if principal.user_id is None:
        organization = await session.get(Organization, principal.organization_id)
        return [WorkspaceOut(organization=OrganizationOut.from_model(organization), role=None, current=True)] if organization else []
    me = await _signed_in_user(principal, session)
    memberships = await active_memberships(session, me.email)
    organizations = {
        org.id: org
        for org in await session.scalars(
            select(Organization).where(Organization.id.in_([m.organization_id for m in memberships]))
        )
    }
    return [
        WorkspaceOut(
            organization=OrganizationOut.from_model(organizations[m.organization_id]),
            role=m.role,
            current=m.organization_id == principal.organization_id,
        )
        for m in memberships
    ]


@router.post(
    "/workspaces/switch",
    response_model=SessionOut,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def switch_workspace(
    payload: SwitchWorkspaceRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
    settings: SettingsDep,
) -> SessionOut:
    """Continue in another workspace this account belongs to. Sets a new session cookie."""
    me = await _signed_in_user(principal, session)
    memberships = await active_memberships(session, me.email)
    target = next((m for m in memberships if m.organization_id == payload.organization_id), None)
    if target is None:
        raise NotFound("You don't have access to that workspace.")
    target.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_session_cookie(response, request, settings, target)
    organization = await session.get(Organization, target.organization_id)
    return SessionOut(method="session", user=UserOut.from_model(target), organization=OrganizationOut.from_model(organization))


@router.post(
    "/workspaces",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse, "description": "Workspace limit reached."}},
)
async def new_workspace(
    payload: CreateWorkspaceRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
    settings: SettingsDep,
) -> SessionOut:
    """Create another workspace owned by the signed-in account, and switch to it."""
    me = await _signed_in_user(principal, session)
    owner = await create_workspace(session, settings, email=me.email, display_name=me.full_name, name=payload.name)
    owner.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_session_cookie(response, request, settings, owner)
    organization = await session.get(Organization, owner.organization_id)
    return SessionOut(method="session", user=UserOut.from_model(owner), organization=OrganizationOut.from_model(organization))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, settings: SettingsDep) -> Response:
    """Clear the session cookie. Safe to call when not signed in."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.session_cookie_name, path="/", secure=_secure(_base_url(request, settings)), samesite="lax", httponly=True)
    return response
