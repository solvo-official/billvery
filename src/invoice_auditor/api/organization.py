from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..enums import ApiScope
from ..errors import NotFound
from ..models import Organization, User
from ..schemas.api import OrganizationOut, UserOut
from .deps import Principal, SessionDep, require_scope

router = APIRouter(prefix="/api/v1", tags=["organization"])

ReadPrincipal = Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))]


@router.get("/organization", response_model=OrganizationOut)
async def get_organization(principal: ReadPrincipal, session: SessionDep) -> OrganizationOut:
    """The organization this API key belongs to."""
    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFound("Organization not found.")
    return OrganizationOut.from_model(organization)


@router.get("/users", response_model=list[UserOut])
async def list_users(principal: ReadPrincipal, session: SessionDep) -> list[UserOut]:
    """Active members of the organization. `can_resolve` marks who may resolve anomalies."""
    users = await session.scalars(
        select(User)
        .where(User.organization_id == principal.organization_id, User.is_active.is_(True))
        .order_by(User.full_name, User.email)
    )
    return [UserOut.from_model(user) for user in users]
