"""Workspaces: each is an organization with its own invoices, members and settings.

A Google account belongs to any number of workspaces through one `users` row per workspace (the
same email in each). Signing in for the first time without any membership creates a private
workspace owned by that account; being invited adds a membership to someone else's.
"""

from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..enums import UserRole
from ..errors import Conflict, Forbidden
from ..models import Organization, User


async def active_memberships(session: AsyncSession, email: str) -> list[User]:
    """Every active membership of an email in an active workspace, most recently used first."""
    rows = await session.scalars(
        select(User)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.email == email.strip().lower(), User.is_active.is_(True), Organization.is_active.is_(True))
        .order_by(nulls_last(User.last_login_at.desc()), User.created_at)
    )
    return list(rows)


async def lock_account(session: AsyncSession, email: str) -> None:
    """Serialize workspace creation per email for the rest of the transaction.

    Two sign-ins racing for the same new account would otherwise both find no membership and
    create two workspaces.
    """
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"workspace:{email.strip().lower()}"))))


def default_workspace_name(email: str, display_name: str | None) -> str:
    first = (display_name or "").strip().split(" ")[0] or email.split("@", 1)[0]
    return f"{first}'s workspace"[:255]


async def create_workspace(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    display_name: str | None,
    name: str | None = None,
) -> User:
    """A new workspace owned by this account. The caller commits."""
    email = email.strip().lower()
    if not settings.signup_allowed(email):
        raise Forbidden("This Google account isn't allowed to create a workspace here. Ask to be invited instead.")
    await lock_account(session, email)
    owned = await session.scalar(
        select(func.count())
        .select_from(User)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.email == email, User.role == UserRole.OWNER.value, Organization.is_active.is_(True))
    )
    if owned >= settings.max_workspaces_per_account:
        raise Conflict(f"You already own {owned} workspaces, the most one account can create.")
    organization = Organization(
        name=(name or default_workspace_name(email, display_name)).strip()[:255],
        currency_code=settings.workspace_default_currency.upper(),
    )
    session.add(organization)
    await session.flush()
    owner = User(
        organization_id=organization.id,
        email=email,
        role=UserRole.OWNER.value,
        full_name=(display_name or "").strip()[:150] or None,
    )
    session.add(owner)
    await session.flush()
    return owner
