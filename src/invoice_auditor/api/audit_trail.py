from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..enums import ApiScope
from ..errors import NotFound
from ..models import Invoice
from ..schemas.api import AuditLogOut, ErrorResponse, IntegrityOut
from ..services.audit_trail import trail, verify_document
from .deps import Principal, SessionDep, require_scope
from .invoices import document_store

router = APIRouter(prefix="/api/v1/invoices", tags=["audit trail"])

ReadPrincipal = Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))]


async def _invoice(session: AsyncSession, organization_id: UUID, invoice_id: UUID) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == organization_id))
    if invoice is None:
        raise NotFound("Invoice not found.")
    return invoice


@router.get("/{invoice_id}/audit-trail", response_model=list[AuditLogOut], responses={404: {"model": ErrorResponse}})
async def get_audit_trail(invoice_id: UUID, principal: ReadPrincipal, session: SessionDep) -> list[AuditLogOut]:
    """The invoice's chain of custody, oldest first: ingestion with the file's SHA-256, then every
    reviewer decision with its signer, note and the status before and after."""
    await _invoice(session, principal.organization_id, invoice_id)
    return [AuditLogOut.from_model(entry) for entry in await trail(session, principal.organization_id, invoice_id)]


@router.get("/{invoice_id}/integrity", response_model=IntegrityOut, responses={404: {"model": ErrorResponse}})
async def verify_invoice_document(
    invoice_id: UUID, request: Request, response: Response, principal: ReadPrincipal, session: SessionDep
) -> IntegrityOut:
    """Tamper check: re-hash the stored original and compare it with the SHA-256 recorded at
    ingestion, on the invoice and in its immutable audit entries."""
    invoice = await _invoice(session, principal.organization_id, invoice_id)
    report = await verify_document(session, request.app.state.db, document_store(request), invoice)
    response.headers["Cache-Control"] = "no-store"
    return IntegrityOut(
        invoice_id=invoice.id,
        status=report.status,
        recorded_sha256=report.recorded_sha256,
        ingested_sha256=report.ingested_sha256,
        computed_sha256=report.computed_sha256,
        computed_size_bytes=report.computed_size_bytes,
        trail_consistent=report.trail_consistent,
        checked_at=report.checked_at,
        message=report.message,
    )
