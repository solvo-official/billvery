import json
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import AnomalyStatus, AnomalyType, InvoiceStatus, Severity, UserRole
from ..models import AnomalyLog, Invoice, InvoiceItem, Organization, User, Vendor
from .extraction import ExtractedLineItem, InvoiceExtraction
from .types import AmountOut, OptionalText2000

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024


class FileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sha256: str = Field(..., pattern=r"^[0-9a-fA-F]{64}$", description="SHA-256 of the file bytes, hex encoded.")
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: Literal["application/pdf", "image/jpeg", "image/png"]
    size_bytes: int = Field(..., gt=0, le=MAX_FILE_BYTES)
    storage_url: str | None = Field(None, max_length=2048, description="Where the original document is stored.")

    @field_validator("sha256")
    @classmethod
    def _lowercase_hash(cls, value: str) -> str:
        return value.lower()


class ProcessInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: FileMetadata
    extraction: InvoiceExtraction
    uploaded_by: UUID | None = Field(None, description="User in your organization who submitted the document.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Free-form tags, e.g. department.")

    @field_validator("extraction", mode="before")
    @classmethod
    def _reject_unknown_extraction_fields(cls, value: Any) -> Any:
        # InvoiceExtraction itself must tolerate extra keys (Gemini's schema cannot carry
        # additionalProperties: false), but from API clients an unknown key is a typo -- a
        # silently dropped "tax" would otherwise surface as a false math mismatch.
        if not isinstance(value, dict):
            return value
        unknown = {str(key) for key in value} - InvoiceExtraction.model_fields.keys()
        for index, line in enumerate(value.get("line_items") or []):
            if isinstance(line, dict):
                unknown |= {f"line_items[{index}].{key}" for key in line.keys() - ExtractedLineItem.model_fields.keys()}
        if unknown:
            raise ValueError(f"unknown extraction field(s): {', '.join(sorted(unknown))}")
        return value

    @field_validator("metadata")
    @classmethod
    def _limit_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must serialize to at most {MAX_METADATA_BYTES} bytes")
        return value


class ResolveAnomalyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal[AnomalyStatus.DISMISSED, AnomalyStatus.CONFIRMED] = Field(
        ..., description="DISMISSED: false positive or accepted. CONFIRMED: the problem is real."
    )
    resolved_by: UUID = Field(..., description="Reviewer (owner, admin or reviewer role) in your organization.")
    note: OptionalText2000 = None


class ApproveInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: UUID = Field(..., description="Reviewer (owner, admin or reviewer role) in your organization.")
    note: OptionalText2000 = Field(None, description="Recorded on every finding the approval dismisses.")


class VendorSummary(BaseModel):
    id: UUID | None
    name: str | None
    tax_id: str | None
    confidence: float | None


class FinancialSummary(BaseModel):
    currency: str
    subtotal: AmountOut | None
    tax: AmountOut
    shipping: AmountOut
    discount: AmountOut
    total: AmountOut | None


class AnomalyOut(BaseModel):
    id: UUID
    type: AnomalyType
    severity: Severity
    confidence: float
    description: str
    detected_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None
    status: AnomalyStatus
    resolved_by: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, anomaly: AnomalyLog) -> "AnomalyOut":
        return cls(
            id=anomaly.id,
            type=AnomalyType(anomaly.anomaly_type),
            severity=Severity(anomaly.severity),
            confidence=float(anomaly.confidence_score),
            description=anomaly.description,
            detected_value=anomaly.detected_value,
            expected_value=anomaly.expected_value,
            status=AnomalyStatus(anomaly.status),
            resolved_by=anomaly.resolved_by,
            resolved_at=anomaly.resolved_at,
            resolution_note=anomaly.resolution_note,
            created_at=anomaly.created_at,
        )


class LineItemOut(BaseModel):
    line_number: int
    description: str | None
    quantity: AmountOut
    unit_price: AmountOut
    line_total: AmountOut


class DocumentOut(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_url: str | None
    available: bool = Field(description="The original can be downloaded from GET /api/v1/invoices/{id}/document.")


class VendorOnFile(BaseModel):
    id: UUID
    name: str
    tax_id: str | None = Field(description="Normalized: upper-case alphanumerics.")
    first_seen: bool = Field(description="The vendor record was created by this invoice.")


class UserRef(BaseModel):
    id: UUID
    name: str


def display_name(user: User) -> str:
    return user.full_name or user.email.split("@", 1)[0]


class ApprovalOut(BaseModel):
    at: datetime
    by: UserRef | None = Field(description="The reviewer; null when the rule engine approved automatically.")
    method: Literal["automatic", "reviewer"]


class InvoiceAuditResponse(BaseModel):
    invoice_id: UUID
    status: InvoiceStatus
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    vendor: VendorSummary
    vendor_on_file: VendorOnFile | None
    financial_summary: FinancialSummary
    line_items: list[LineItemOut]
    ai_confidence: float | None
    anomalies: list[AnomalyOut] = Field(description="Most severe first.")
    document: DocumentOut
    source: Literal["upload", "api"] = Field(description="upload: submitted by a person; api: by an integration.")
    submitted_by: UserRef | None
    approval: ApprovalOut | None = Field(description="Present once the invoice is APPROVED.")
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_models(
        cls,
        invoice: Invoice,
        anomalies: Iterable[AnomalyLog],
        *,
        items: Iterable[InvoiceItem] = (),
        vendor: Vendor | None = None,
        uploader: User | None = None,
        approver: User | None = None,
        document_available: bool = False,
    ) -> "InvoiceAuditResponse":
        ordered = sorted(anomalies, key=lambda a: (-Severity(a.severity).rank, a.created_at, a.anomaly_type))
        return cls(
            invoice_id=invoice.id,
            status=InvoiceStatus(invoice.status),
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            vendor=VendorSummary(
                id=invoice.vendor_id,
                name=invoice.vendor_name,
                tax_id=invoice.vendor_tax_id,
                confidence=invoice.ai_extraction.get("vendor_confidence"),
            ),
            financial_summary=FinancialSummary(
                currency=invoice.currency,
                subtotal=invoice.subtotal,
                tax=invoice.tax_amount,
                shipping=invoice.shipping_amount,
                discount=invoice.discount_amount,
                total=invoice.total_amount,
            ),
            vendor_on_file=(
                VendorOnFile(
                    id=vendor.id,
                    name=vendor.name,
                    tax_id=vendor.tax_id,
                    # Created in the same transaction as the invoice, so the timestamps match.
                    first_seen=vendor.created_at >= invoice.created_at,
                )
                if vendor is not None
                else None
            ),
            line_items=[
                LineItemOut(
                    line_number=item.line_number,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    line_total=item.line_total,
                )
                for item in sorted(items, key=lambda item: item.line_number)
            ],
            ai_confidence=float(invoice.ai_confidence) if invoice.ai_confidence is not None else None,
            anomalies=[AnomalyOut.from_model(a) for a in ordered],
            document=DocumentOut(
                filename=invoice.source_file_name,
                content_type=invoice.file_mime_type,
                size_bytes=invoice.file_size_bytes,
                sha256=invoice.file_hash,
                storage_url=invoice.source_file_url,
                available=document_available,
            ),
            source="upload" if invoice.uploaded_by is not None else "api",
            submitted_by=UserRef(id=uploader.id, name=display_name(uploader)) if uploader is not None else None,
            approval=(
                ApprovalOut(
                    at=invoice.approved_at,
                    by=UserRef(id=approver.id, name=display_name(approver)) if approver is not None else None,
                    method="reviewer" if invoice.approved_by is not None else "automatic",
                )
                if invoice.approved_at is not None
                else None
            ),
            processed_at=invoice.processed_at,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
        )


class InvoicePage(BaseModel):
    items: list[InvoiceAuditResponse] = Field(description="Newest first.")
    next_cursor: str | None = Field(description="Pass as `cursor` for the next page; null on the last page.")
    server_time: datetime = Field(description="Database time of this response. Poll with updated_since set a little before it.")


class OrganizationOut(BaseModel):
    id: UUID
    name: str
    legal_name: str | None
    country_code: str | None
    currency_code: str
    subscription_plan: str

    @classmethod
    def from_model(cls, organization: Organization) -> "OrganizationOut":
        return cls(
            id=organization.id,
            name=organization.name,
            legal_name=organization.legal_name,
            country_code=organization.country_code,
            currency_code=organization.currency_code,
            subscription_plan=organization.subscription_plan,
        )


class UserOut(BaseModel):
    id: UUID
    name: str
    email: str
    role: UserRole
    can_resolve: bool

    @classmethod
    def from_model(cls, user: User) -> "UserOut":
        role = UserRole(user.role)
        return cls(id=user.id, name=display_name(user), email=user.email, role=role, can_resolve=role.can_resolve_anomalies)


class ExtractionHealth(BaseModel):
    configured: bool
    model: str


class AuthHealth(BaseModel):
    """Which ways of signing in this deployment offers."""

    google: bool = Field(description="Google sign-in is configured; the console can show its button.")
    session: bool = Field(description="Session cookies are signed (JWT_SECRET is set).")


class LimitsHealth(BaseModel):
    max_upload_bytes: int = Field(description="Largest file POST /invoices/upload accepts here.")


class HealthOut(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok"]
    extraction: ExtractionHealth
    auth: AuthHealth
    limits: LimitsHealth


class SessionOut(BaseModel):
    """Who the caller is, for the console's sign-in gate."""

    method: Literal["api_key", "session"]
    user: "UserOut | None" = Field(description="The signed-in person; null when an API key authenticated.")
    organization: "OrganizationOut"
    expires_at: datetime | None = None


class ResolveAnomalyResponse(BaseModel):
    anomaly: AnomalyOut
    invoice_id: UUID
    invoice_status: InvoiceStatus


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
