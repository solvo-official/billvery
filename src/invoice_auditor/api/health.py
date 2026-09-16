from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import __version__
from ..errors import ServiceUnavailable
from ..schemas.api import AuthHealth, ExtractionHealth, HealthOut, LimitsHealth
from .deps import SessionDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthOut)
async def healthz(request: Request, session: SessionDep, settings: SettingsDep) -> HealthOut:
    """Liveness, database connectivity and whether uploads can be extracted. No auth required."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        raise ServiceUnavailable("Database is unreachable.") from exc
    extractor = getattr(request.app.state, "extractor", None)
    return HealthOut(
        status="ok",
        version=__version__,
        database="ok",
        extraction=ExtractionHealth(configured=extractor is not None, model=settings.gemini_model),
        auth=AuthHealth(google=settings.google_login_configured, session=settings.jwt_secret is not None),
        limits=LimitsHealth(max_upload_bytes=settings.max_upload_bytes),
    )
