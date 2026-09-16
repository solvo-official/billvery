import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..enums import AnomalyStatus, InvoiceStatus, UserRole
from ..errors import Conflict, Forbidden, NotFound
from ..models import AnomalyLog, Invoice, User
from ..schemas.api import ApproveInvoiceRequest, ResolveAnomalyRequest
from .anomaly_detection import decide_invoice_status, utcnow
from .tenancy import get_active_member

logger = logging.getLogger(__name__)

DEFAULT_APPROVAL_NOTE = "Approved and archived."

NOT_APPROVABLE = {
    InvoiceStatus.APPROVED: "This invoice is already approved.",
    InvoiceStatus.REJECTED: "This invoice was rejected: a reviewer confirmed a blocking finding, and confirmed findings are final.",
    InvoiceStatus.PROCESSING: "This invoice is still being processed.",
}


@dataclass(frozen=True, slots=True)
class ResolveOutcome:
    anomaly: AnomalyLog
    invoice: Invoice


class AnomalyReviewService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.session = session
        self.clock = clock

    async def resolve(
        self, organization_id: UUID, anomaly_id: UUID, request: ResolveAnomalyRequest
    ) -> ResolveOutcome:
        """Record a reviewer's decision on one open anomaly and re-derive the invoice status."""
        try:
            outcome = await self._resolve(organization_id, anomaly_id, request)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return outcome

    async def approve(self, organization_id: UUID, invoice_id: UUID, request: ApproveInvoiceRequest) -> Invoice:
        """Approve an invoice held for review: dismiss every open finding and stamp the approval."""
        try:
            invoice = await self._approve(organization_id, invoice_id, request)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return invoice

    async def _reviewer(self, organization_id: UUID, user_id: UUID) -> User:
        reviewer = await get_active_member(self.session, organization_id, user_id)
        if not UserRole(reviewer.role).can_resolve_anomalies:
            raise Forbidden(f"Users with role '{reviewer.role}' cannot resolve anomalies or approve invoices.")
        return reviewer

    async def _statuses(self, invoice: Invoice) -> InvoiceStatus:
        states = (
            await self.session.execute(
                select(AnomalyLog.severity, AnomalyLog.status).where(AnomalyLog.invoice_id == invoice.id)
            )
        ).all()
        return decide_invoice_status(states)

    async def _resolve(
        self, organization_id: UUID, anomaly_id: UUID, request: ResolveAnomalyRequest
    ) -> ResolveOutcome:
        invoice_id = await self.session.scalar(
            select(AnomalyLog.invoice_id).where(
                AnomalyLog.id == anomaly_id, AnomalyLog.organization_id == organization_id
            )
        )
        if invoice_id is None:
            raise NotFound("Anomaly not found.")

        # Lock the invoice first, then the anomaly: concurrent resolutions on the same invoice
        # queue up here and each sees the others' decisions when re-deriving the status.
        invoice = await self.session.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.organization_id == organization_id)
            .with_for_update()
        )
        anomaly = await self.session.scalar(select(AnomalyLog).where(AnomalyLog.id == anomaly_id).with_for_update())
        if invoice is None or anomaly is None:  # both exist unless deleted mid-request
            raise NotFound("Anomaly not found.")
        if anomaly.status != AnomalyStatus.OPEN:
            raise Conflict(
                f"Anomaly is already {anomaly.status}.",
                details={
                    "resolved_by": str(anomaly.resolved_by),
                    "resolved_at": anomaly.resolved_at.isoformat() if anomaly.resolved_at else None,
                },
            )

        reviewer = await self._reviewer(organization_id, request.resolved_by)
        now = self.clock()
        anomaly.status = AnomalyStatus(request.resolution).value
        anomaly.resolved_by = reviewer.id
        anomaly.resolved_at = now
        anomaly.resolution_note = request.note
        await self.session.flush()

        previous_status = invoice.status
        status = await self._statuses(invoice)
        if status is InvoiceStatus.APPROVED and previous_status != InvoiceStatus.APPROVED:
            invoice.approved_at = now
            invoice.approved_by = reviewer.id
        invoice.status = status.value
        # Touch the invoice even when its status is unchanged, so that clients polling
        # GET /invoices?updated_since=... pick up the resolution (the trigger sets the value).
        invoice.updated_at = now
        await self.session.flush()

        logger.info(
            "anomaly %s %s by user %s; invoice %s %s -> %s",
            anomaly.id,
            anomaly.status,
            reviewer.id,
            invoice.id,
            previous_status,
            invoice.status,
        )
        return ResolveOutcome(anomaly=anomaly, invoice=invoice)

    async def _approve(self, organization_id: UUID, invoice_id: UUID, request: ApproveInvoiceRequest) -> Invoice:
        invoice = await self.session.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.organization_id == organization_id)
            .with_for_update()
        )
        if invoice is None:
            raise NotFound("Invoice not found.")
        status = InvoiceStatus(invoice.status)
        if status is not InvoiceStatus.NEEDS_REVIEW:
            raise Conflict(NOT_APPROVABLE[status], details={"status": status.value})

        reviewer = await self._reviewer(organization_id, request.approved_by)
        now = self.clock()
        note = request.note or DEFAULT_APPROVAL_NOTE
        open_findings = (
            await self.session.scalars(
                select(AnomalyLog)
                .where(AnomalyLog.invoice_id == invoice.id, AnomalyLog.status == AnomalyStatus.OPEN.value)
                .with_for_update()
            )
        ).all()
        for finding in open_findings:
            finding.status = AnomalyStatus.DISMISSED.value
            finding.resolved_by = reviewer.id
            finding.resolved_at = now
            finding.resolution_note = note
        await self.session.flush()

        # In review means no blocking finding was confirmed, so with the rest dismissed this is APPROVED.
        if await self._statuses(invoice) is not InvoiceStatus.APPROVED:
            raise Conflict("The invoice's findings no longer allow approval. Reload it and review again.")
        invoice.status = InvoiceStatus.APPROVED.value
        invoice.approved_at = now
        invoice.approved_by = reviewer.id
        invoice.updated_at = now
        await self.session.flush()

        logger.info(
            "invoice %s approved by user %s; %d open finding(s) dismissed", invoice.id, reviewer.id, len(open_findings)
        )
        return invoice
