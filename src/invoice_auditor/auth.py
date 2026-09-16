"""Browser sessions and Google sign-in.

API keys authenticate integrations. A browser cannot hold one safely, so the console signs in
with Google instead and carries a short-lived session cookie: a JWT signed with JWT_SECRET,
holding only the user and organization ids, re-checked against the database on every request.

The OAuth 2.0 authorization code flow is used with PKCE and a state nonce, both kept in a signed,
HttpOnly cookie for the few minutes the redirect takes. Google accounts are not provisioned on
the fly: the email must already belong to an active user of an active organization, so access
stays invite-only through `invoice-auditor create-user`.
"""

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt

from .config import Settings
from .errors import AppError, ServiceUnavailable
from .models import User

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
SESSION_TYP = "session"
STATE_TYP = "oauth-state"
STATE_TTL = timedelta(minutes=10)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
GOOGLE_SCOPES = "openid email profile"
TOKEN_TIMEOUT_S = 10.0


class SignInError(AppError):
    """Sign-in could not be completed; the callback turns this into a redirect with a reason."""

    status_code = 401
    code = "sign_in_failed"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _secret(settings: Settings) -> str:
    if settings.jwt_secret is None:
        raise ServiceUnavailable("Browser sign-in isn't configured on this server: JWT_SECRET is not set.")
    return settings.jwt_secret.get_secret_value()


# --- Session cookie ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionClaims:
    user_id: UUID
    organization_id: UUID
    expires_at: datetime


def issue_session(settings: Settings, user: User, *, now: datetime | None = None) -> tuple[str, datetime]:
    """A signed session token for this user, and when it expires."""
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(hours=settings.session_ttl_hours)
    payload = {
        "typ": SESSION_TYP,
        "sub": str(user.id),
        "org": str(user.organization_id),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, _secret(settings), algorithm=ALGORITHM), expires


def read_session(settings: Settings, token: str | None) -> SessionClaims | None:
    """The claims of a valid, unexpired session token, or None. Never raises on bad input."""
    if settings.jwt_secret is None or not token:
        return None
    try:
        payload = jwt.decode(
            token, _secret(settings), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
        if payload.get("typ") != SESSION_TYP:
            return None
        return SessionClaims(
            user_id=UUID(payload["sub"]),
            organization_id=UUID(payload["org"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


# --- Google authorization code flow ------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def safe_next_path(value: str | None) -> str:
    """Only same-site paths may be returned to, so the callback can't become an open redirect."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


def start_login(settings: Settings, *, redirect_uri: str, next_path: str) -> tuple[str, str]:
    """Return (Google authorization URL, signed state token to store in a cookie)."""
    if not settings.google_client_id:
        raise ServiceUnavailable("Google sign-in isn't configured on this server: GOOGLE_CLIENT_ID is not set.")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "typ": STATE_TYP,
            "state": state,
            "verifier": verifier,
            "next": safe_next_path(next_path),
            "redirect_uri": redirect_uri,
            "iat": int(now.timestamp()),
            "exp": int((now + STATE_TTL).timestamp()),
        },
        _secret(settings),
        algorithm=ALGORITHM,
    )
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_ENDPOINT}?{query}", token


def read_state(settings: Settings, token: str | None) -> dict[str, Any] | None:
    if settings.jwt_secret is None or not token:
        return None
    try:
        payload = jwt.decode(token, _secret(settings), algorithms=[ALGORITHM], options={"require": ["exp"]})
    except jwt.InvalidTokenError:
        return None
    return payload if payload.get("typ") == STATE_TYP else None


async def exchange_code(settings: Settings, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
    """Swap the authorization code for Google's ID token claims (email, name, subject)."""
    if not (settings.google_client_id and settings.google_client_secret):
        raise ServiceUnavailable("Google sign-in isn't configured on this server.")
    form = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret.get_secret_value(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_S) as client:
            response = await client.post(GOOGLE_TOKEN_ENDPOINT, data=form)
    except httpx.HTTPError as exc:
        logger.warning("Google token endpoint unreachable: %s", exc)
        raise SignInError("Could not reach Google to complete sign-in.", reason="google_unreachable") from exc
    if response.status_code != 200:
        logger.warning("Google token exchange failed (%s): %s", response.status_code, response.text[:300])
        raise SignInError("Google rejected the sign-in attempt.", reason="exchange_failed")
    id_token = response.json().get("id_token")
    if not isinstance(id_token, str):
        raise SignInError("Google did not return an ID token.", reason="no_id_token")
    return verify_id_token(settings, id_token)


def verify_id_token(settings: Settings, id_token: str) -> dict[str, Any]:
    """Validate the ID token's claims.

    The token came back over a TLS connection this server opened to Google's token endpoint, so
    the claims may be trusted without re-checking the signature (OpenID Connect Core 3.1.3.7);
    issuer, audience, expiry and a verified email address are still checked here.
    """
    try:
        claims = jwt.decode(
            id_token,
            options={"verify_signature": False, "require": ["exp", "iss", "aud", "sub"]},
            algorithms=["RS256", "ES256"],
            audience=settings.google_client_id,
            issuer=GOOGLE_ISSUERS,
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Google ID token rejected: %s", exc)
        raise SignInError("Google's response could not be verified.", reason="invalid_id_token") from exc
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise SignInError("Google did not share an email address.", reason="no_email")
    if claims.get("email_verified") is not True:
        raise SignInError("That Google account's email address is not verified.", reason="email_unverified")
    return {"email": email, "name": claims.get("name"), "sub": claims.get("sub")}
