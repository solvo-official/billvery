from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from ..enums import ApiScope, InvoiceStatus
from ..schemas.api import AnomalyOut, ErrorResponse, ResolveAnomalyRequest, ResolveAnomalyResponse
from ..services.review import AnomalyReviewService
from .deps import Principal, SessionDep, require_scope

router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


@router.post(
    "/{anomaly_id}/resolve",
    response_model=ResolveAnomalyResponse,
    responses={
        403: {"model": ErrorResponse, "description": "The reviewer's role may not resolve anomalies."},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "The anomaly was already resolved."},
        422: {"model": ErrorResponse, "description": "resolved_by is not an active user of this organization."},
    },
)
async def resolve_anomaly(
    anomaly_id: UUID,
    payload: ResolveAnomalyRequest,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.ANOMALIES_RESOLVE))],
    session: SessionDep,
) -> ResolveAnomalyResponse:
    """Record a reviewer's decision and re-derive the invoice status.

    A CONFIRMED HIGH/CRITICAL anomaly rejects the invoice. Once no HIGH/CRITICAL anomaly is
    left open (and none is confirmed), the invoice is APPROVED.
    """
    principal.acting_as(payload.resolved_by)
    outcome = await AnomalyReviewService(session).resolve(principal.organization_id, anomaly_id, payload)
    return ResolveAnomalyResponse(
        anomaly=AnomalyOut.from_model(outcome.anomaly),
        invoice_id=outcome.invoice.id,
        invoice_status=InvoiceStatus(outcome.invoice.status),
    )
