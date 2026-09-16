from uuid import UUID

from sqlalchemy import BigInteger, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import InvalidReference
from ..models import User


async def lock_organization(session: AsyncSession, organization_id: UUID) -> None:
    """Serialize invoice processing per tenant until the current transaction ends.

    Duplicate detection is check-then-insert: without this lock, two concurrent submissions of
    the same invoice could both pass the duplicate checks.
    """
    key = int.from_bytes(organization_id.bytes[:8], "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(literal(key, BigInteger))))


async def get_active_member(session: AsyncSession, organization_id: UUID, user_id: UUID) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.organization_id == organization_id, User.is_active.is_(True))
    )
    if user is None:
        raise InvalidReference(f"User {user_id} is not an active member of this organization.")
    return user
