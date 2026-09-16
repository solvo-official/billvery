import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..enums import AnomalyStatus, InvoiceStatus
from ..errors import AppError, Conflict, NotFound
from ..models import AnomalyLog, Invoice, InvoiceItem, Organization
from ..money import ZERO, quantize
from ..schemas.api import ProcessInvoiceRequest
from ..schemas.extraction import ExtractedLineItem
from .anomaly_detection import AnomalyDetectionService, Finding, VendorResolution, decide_invoice_status, utcnow
from .normalization import normalize_description, normalize_invoice_number
from .tenancy import get_active_member, lock_organization

logger = logging.getLogger(__name__)

ONE = Decimal("1.0000")


class UnprocessableExtraction(AppError):
    status_code = 422
    code = "invalid_extraction"


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    invoice_id: UUID
    replayed: bool  # True when an Idempotency-Key matched an earlier request


class InvoiceProcessor:
    """Stores one extracted invoice and its audit findings in a single transaction.

    The state machine's PROCESSING state belongs to the upstream extraction step; by the time
    the extraction reaches this service the invoice goes straight to NEEDS_REVIEW or APPROVED.
    """

    def __init__(self, session: AsyncSession, settings: Settings, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.session = session
        self.settings = settings
        self.clock = clock

    async def process(
        self, organization_id: UUID, request: ProcessInvoiceRequest, *, idempotency_key: str | None = None
    ) -> ProcessOutcome:
        try:
            outcome = await self._process(organization_id, request, idempotency_key)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        return outcome

    async def _process(
        self, organization_id: UUID, request: ProcessInvoiceRequest, idempotency_key: str | None
    ) -> ProcessOutcome:
        await lock_organization(self.session, organization_id)
        organization = await self.session.get(Organization, organization_id)
        if organization is None or not organization.is_active:
            raise NotFound("Organization not found.")

        if idempotency_key is not None:
            existing = await self.session.scalar(
                select(Invoice).where(Invoice.organization_id == organization_id, Invoice.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if existing.file_hash != request.file.sha256:
                    raise Conflict(
                        "This Idempotency-Key was already used for a different file.",
                        details={"invoice_id": str(existing.id)},
                    )
                return ProcessOutcome(existing.id, replayed=True)

        if request.uploaded_by is not None:
            await get_active_member(self.session, organization_id, request.uploaded_by)

        extraction = request.extraction
        detector = AnomalyDetectionService(self.session, self.settings, clock=self.clock)
        vendors = await detector.resolve_vendor(organization_id, extraction.vendor_name, extraction.vendor_tax_id)

        invoice = self._build_invoice(organization, request, vendors, idempotency_key)
        items = self._build_items(invoice, extraction.line_items)
        findings = await detector.detect(invoice, items, organization, vendors)

        invoice.status = decide_invoice_status((f.severity, AnomalyStatus.OPEN) for f in findings).value
        invoice.processed_at = self.clock()
        if invoice.status == InvoiceStatus.APPROVED:
            invoice.approved_at = invoice.processed_at  # approved automatically by the rule engine
        invoice.items = items
        invoice.anomalies = [self._anomaly_row(invoice, finding) for finding in findings]
        self.session.add(invoice)
        await self.session.flush()

        logger.info(
            "processed invoice %s for organization %s: status=%s anomalies=%s",
            invoice.id,
            organization_id,
            invoice.status,
            ",".join(f.anomaly_type.value for f in findings) or "none",
        )
        return ProcessOutcome(invoice.id, replayed=False)

    def _build_invoice(
        self,
        organization: Organization,
        request: ProcessInvoiceRequest,
        vendors: VendorResolution,
        idempotency_key: str | None,
    ) -> Invoice:
        extraction = request.extraction
        return Invoice(
            id=uuid.uuid4(),  # assigned up front: the rules exclude the invoice under audit by id
            organization_id=organization.id,
            vendor_id=vendors.vendor.id if vendors.vendor is not None else None,
            uploaded_by=request.uploaded_by,
            vendor_name=extraction.vendor_name,
            vendor_tax_id=extraction.vendor_tax_id,
            invoice_number=extraction.invoice_number,
            invoice_number_normalized=normalize_invoice_number(extraction.invoice_number),
            invoice_date=extraction.invoice_date,
            due_date=extraction.due_date,
            currency=extraction.currency or organization.currency_code,
            subtotal=extraction.subtotal,
            tax_amount=extraction.tax_amount or ZERO,
            shipping_amount=extraction.shipping_amount or ZERO,
            discount_amount=extraction.discount_amount or ZERO,
            total_amount=extraction.total_amount,
            status=InvoiceStatus.PROCESSING.value,
            ai_confidence=Decimal(str(extraction.confidence)).quantize(Decimal("0.01"), ROUND_HALF_UP),
            ai_extraction=extraction.model_dump(mode="json"),
            source_file_url=request.file.storage_url,
            source_file_name=request.file.filename,
            file_mime_type=request.file.content_type,
            file_size_bytes=request.file.size_bytes,
            file_hash=request.file.sha256,
            idempotency_key=idempotency_key,
            metadata_=request.metadata,
        )

    @staticmethod
    def _build_items(invoice: Invoice, lines: Sequence[ExtractedLineItem]) -> list[InvoiceItem]:
        items: list[InvoiceItem] = []
        for number, line in enumerate(lines, start=1):
            quantity = line.quantity if line.quantity is not None else ONE
            unit_price, line_total = line.unit_price, line.line_total
            try:
                if line_total is None:
                    line_total = quantize(quantity * unit_price)
                elif unit_price is None:
                    unit_price = quantize(line_total / quantity) if quantity != 0 else line_total
            except ValueError as exc:
                raise UnprocessableExtraction(f"Line item {number}: {exc}") from exc
            items.append(
                InvoiceItem(
                    id=uuid.uuid4(),
                    organization_id=invoice.organization_id,
                    line_number=number,
                    description=line.description,
                    description_normalized=normalize_description(line.description),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                    extracted_data=line.model_dump(mode="json"),
                )
            )
        return items

    @staticmethod
    def _anomaly_row(invoice: Invoice, finding: Finding) -> AnomalyLog:
        return AnomalyLog(
            id=uuid.uuid4(),
            organization_id=invoice.organization_id,
            anomaly_type=finding.anomaly_type.value,
            severity=finding.severity.value,
            confidence_score=finding.confidence.quantize(Decimal("0.01"), ROUND_HALF_UP),
            description=finding.description,
            detected_value=finding.detected_value,
            expected_value=finding.expected_value,
            status=AnomalyStatus.OPEN.value,
        )
