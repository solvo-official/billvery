from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..enums import ApiScope, UserRole
from ..errors import Conflict, Forbidden, NotFound
from ..models import Organization, User
from ..schemas.api import ErrorResponse, InviteUserRequest, OrganizationOut, UpdateUserRequest, UserOut
from .deps import Principal, SessionDep, require_scope

router = APIRouter(prefix="/api/v1", tags=["organization"])

ReadPrincipal = Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))]

MANAGERS = frozenset({UserRole.OWNER, UserRole.ADMIN})


@router.get("/organization", response_model=OrganizationOut)
async def get_organization(principal: ReadPrincipal, session: SessionDep) -> OrganizationOut:
    """The organization this API key belongs to."""
    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFound("Organization not found.")
    return OrganizationOut.from_model(organization)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    principal: ReadPrincipal,
    session: SessionDep,
    include_inactive: Annotated[bool, Query(description="Also list people whose access was removed.")] = False,
) -> list[UserOut]:
    """Members of the organization. `can_resolve` marks who may resolve anomalies."""
    stmt = select(User).where(User.organization_id == principal.organization_id).order_by(User.full_name, User.email)
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    return [UserOut.from_model(user) for user in await session.scalars(stmt)]


# --- Team management -----------------------------------------------------------------------------


async def get_manager(principal: ReadPrincipal, session: SessionDep) -> User:
    """The signed-in owner or admin making a team change."""
    if principal.user_id is None:
        raise Forbidden("Sign in with Google as an owner or admin to manage the team.")
    manager = await session.get(User, principal.user_id)
    if manager is None or UserRole(manager.role) not in MANAGERS:
        raise Forbidden("Only owners and admins can manage the team.")
    return manager


ManagerDep = Annotated[User, Depends(get_manager)]


def _guard_owner_changes(manager: User, *, target_role: UserRole, new_role: UserRole | None) -> None:
    """Admins manage everyone except owners, and cannot create owners."""
    if UserRole(manager.role) is UserRole.OWNER:
        return
    if target_role is UserRole.OWNER or new_role is UserRole.OWNER:
        raise Forbidden("Only an owner can add, change or remove owners.")


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse, "description": "That email is already a member."}},
)
async def invite_user(payload: InviteUserRequest, manager: ManagerDep, session: SessionDep) -> UserOut:
    """Invite someone: their Google account can sign in straight away. No email is sent."""
    _guard_owner_changes(manager, target_role=payload.role, new_role=payload.role)
    existing = await session.scalar(
        select(User).where(User.organization_id == manager.organization_id, User.email == payload.email)
    )
    if existing is not None:
        hint = "They already have access." if existing.is_active else "Their access was removed; restore it instead."
        raise Conflict(f"{payload.email} is already on the team. {hint}", details={"user_id": str(existing.id), "active": existing.is_active})
    user = User(organization_id=manager.organization_id, email=payload.email, role=payload.role.value, full_name=payload.name or None)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:  # a concurrent invite of the same address
        await session.rollback()
        raise Conflict(f"{payload.email} is already on the team.") from exc
    return UserOut.from_model(user)


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def update_user(user_id: UUID, payload: UpdateUserRequest, manager: ManagerDep, session: SessionDep) -> UserOut:
    """Change someone's role, or remove and restore their access."""
    user = await session.scalar(
        select(User).where(User.id == user_id, User.organization_id == manager.organization_id).with_for_update()
    )
    if user is None:
        raise NotFound("That person isn't on this team.")
    if user.id == manager.id:
        raise Forbidden("You can't change your own role or access. Ask another owner or admin.")
    current = UserRole(user.role)
    _guard_owner_changes(manager, target_role=current, new_role=payload.role)

    losing_owner = current is UserRole.OWNER and (
        (payload.role is not None and payload.role is not UserRole.OWNER) or payload.active is False
    )
    if losing_owner and user.is_active:
        other_owners = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == manager.organization_id,
                User.role == UserRole.OWNER.value,
                User.is_active.is_(True),
                User.id != user.id,
            )
        )
        if not other_owners:
            raise Conflict("The team needs at least one active owner.")

    if payload.role is not None:
        user.role = payload.role.value
    if payload.active is not None:
        user.is_active = payload.active
    await session.commit()
    return UserOut.from_model(user)
