"""SQLAlchemy 2.0 models. The schema itself is owned by the Alembic migrations in `migrations/`.

Tenant isolation is enforced by the database as well as the application: every child row that
carries an `organization_id` references its parent through a composite foreign key on
(id, organization_id), so a vendor, line item or anomaly can never point at another tenant's row.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import AnomalyStatus, ApiScope, AuditAction, InvoiceStatus, Severity, UserRole
from .money import ZERO

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(18, 4)
Confidence = Numeric(5, 2)


def _one_of(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in enum)})"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


def _jsonb_object() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: DateTime(timezone=True), dict[str, Any]: JSONB}


class TimestampMixin:
    # updated_at is maintained by the set_updated_at() trigger, so raw SQL updates bump it too.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso"),
        CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="currency_code_iso"),
        Index("ix_organizations_name", "name"),
        Index("ix_organizations_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))
    legal_name: Mapped[str | None] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str | None] = mapped_column(CHAR(2))  # ISO 3166-1 alpha-2
    currency_code: Mapped[str] = mapped_column(CHAR(3), default="USD", server_default="USD")
    subscription_plan: Mapped[str] = mapped_column(String(50), default="starter", server_default="starter")
    # Recognised keys: "max_tax_rate_percent" overrides tax_rate_limits for this tenant.
    settings: Mapped[dict[str, Any]] = _jsonb_object()
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email"),
        Index("ix_users_email", "email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(_one_of("role", UserRole), name="role_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.MEMBER.value, server_default=UserRole.MEMBER.value)
    permissions: Mapped[dict[str, Any]] = _jsonb_object()
    last_login_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


DEFAULT_API_KEY_SCOPES = [scope.value for scope in ApiScope]


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_sha256"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(16))  # shown in UIs to identify a key
    key_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)  # SHA-256 of the full key
    permissions: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=lambda: {"scopes": list(DEFAULT_API_KEY_SCOPES)},
        server_default=text("'{}'::jsonb"),
    )
    last_used_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Vendor(TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_name"),
        UniqueConstraint("id", "organization_id"),  # target of the composite FK from invoices
        Index(
            "uq_vendors_organization_id_tax_id",
            "organization_id",
            "tax_id",
            unique=True,
            postgresql_where=text("tax_id IS NOT NULL"),
        ),
        Index(
            "ix_vendors_normalized_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
        CheckConstraint("normalized_name <> ''", name="normalized_name_not_empty"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))  # display name, as first seen
    normalized_name: Mapped[str] = mapped_column(String(255))  # see services.normalization
    tax_id: Mapped[str | None] = mapped_column(String(100))  # normalized: upper-case alphanumerics


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),  # target of composite FKs from child tables
        # RESTRICT: a vendor with invoices cannot be deleted. Tenant deletion still works because
        # every RI check fires at the end of the cascading DELETE statement.
        ForeignKeyConstraint(
            ["vendor_id", "organization_id"],
            ["vendors.id", "vendors.organization_id"],
            ondelete="RESTRICT",
            name="fk_invoices_vendor_id_organization_id_vendors",
        ),
        Index("ix_invoices_organization_id_file_hash", "organization_id", "file_hash"),
        Index(
            "ix_invoices_duplicate_lookup",
            "organization_id",
            "vendor_id",
            "invoice_number_normalized",
            "created_at",
        ),
        Index("ix_invoices_organization_id_status_created_at", "organization_id", "status", "created_at"),
        Index("ix_invoices_organization_id_invoice_date", "organization_id", "invoice_date"),
        Index("ix_invoices_organization_id_created_at_id", "organization_id", "created_at", "id"),  # feed pages
        Index("ix_invoices_organization_id_updated_at", "organization_id", "updated_at"),  # live polling
        Index("ix_invoices_organization_id_approved_at", "organization_id", "approved_at"),  # approved archive
        CheckConstraint(
            "(status = 'APPROVED' AND approved_at IS NOT NULL)"
            " OR (status <> 'APPROVED' AND approved_at IS NULL AND approved_by IS NULL)",
            name="approval_consistent",
        ),
        Index(
            "uq_invoices_organization_id_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint(_one_of("status", InvoiceStatus), name="status_valid"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint("file_hash ~ '^[0-9a-f]{64}$'", name="file_hash_sha256"),
        CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
        CheckConstraint("ai_confidence BETWEEN 0 AND 100", name="ai_confidence_range"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    vendor_id: Mapped[uuid.UUID | None]
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    vendor_name: Mapped[str | None] = mapped_column(String(255))  # as printed on the document
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100))  # as printed on the document
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_number_normalized: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None]
    due_date: Mapped[date | None]
    currency: Mapped[str] = mapped_column(CHAR(3))

    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=ZERO, server_default="0")
    shipping_amount: Mapped[Decimal] = mapped_column(Money, default=ZERO, server_default="0")
    discount_amount: Mapped[Decimal] = mapped_column(Money, default=ZERO, server_default="0")
    total_amount: Mapped[Decimal | None] = mapped_column(Money)

    status: Mapped[str] = mapped_column(
        String(20), default=InvoiceStatus.PROCESSING.value, server_default=InvoiceStatus.PROCESSING.value
    )
    ai_confidence: Mapped[Decimal | None] = mapped_column(Confidence)
    ai_extraction: Mapped[dict[str, Any]] = _jsonb_object()

    source_file_url: Mapped[str | None] = mapped_column(Text)
    source_file_name: Mapped[str] = mapped_column(String(255))
    file_mime_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(CHAR(64))  # SHA-256, lower-case hex

    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    # "metadata" is reserved on declarative classes, hence the trailing underscore.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    processed_at: Mapped[datetime | None]
    # Set when the invoice becomes APPROVED. approved_by is the reviewer, or empty when the rule
    # engine approved it automatically. A trigger stamps approved_at if a writer forgets.
    approved_at: Mapped[datetime | None]
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", lazy="raise", order_by="InvoiceItem.line_number", passive_deletes=True
    )
    anomalies: Mapped[list["AnomalyLog"]] = relationship(
        back_populates="invoice", lazy="raise", order_by="AnomalyLog.created_at", passive_deletes="all"
    )


class InvoiceItem(TimestampMixin, Base):
    __tablename__ = "invoice_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="CASCADE",
            name="fk_invoice_items_invoice_id_organization_id_invoices",
        ),
        UniqueConstraint("invoice_id", "line_number"),
        Index("ix_invoice_items_price_history", "organization_id", "description_normalized"),
        Index(
            "ix_invoice_items_description_fts",
            text("to_tsvector('english', coalesce(description, ''))"),
            postgresql_using="gin",
        ),
        CheckConstraint("line_number > 0", name="line_number_positive"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID]
    organization_id: Mapped[uuid.UUID]
    line_number: Mapped[int]
    description: Mapped[str | None] = mapped_column(Text)
    description_normalized: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(Money, default=Decimal("1.0000"), server_default="1")
    unit_price: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)
    extracted_data: Mapped[dict[str, Any]] = _jsonb_object()

    invoice: Mapped[Invoice] = relationship(back_populates="items", lazy="raise")


class AnomalyLog(Base):
    """An audit finding. Findings are immutable; only the resolution fields may be set, once.

    The migration installs triggers that enforce this and block deletes, except when the whole
    tenant is being removed.
    """

    __tablename__ = "anomaly_logs"
    __table_args__ = (
        # RESTRICT: an invoice that has findings cannot be deleted, so its audit trail survives.
        ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="RESTRICT",
            name="fk_anomaly_logs_invoice_id_organization_id_invoices",
        ),
        Index("ix_anomaly_logs_invoice_id", "invoice_id"),
        Index("ix_anomaly_logs_organization_id_status_severity", "organization_id", "status", "severity"),
        Index("ix_anomaly_logs_organization_id_anomaly_type", "organization_id", "anomaly_type"),
        CheckConstraint(_one_of("severity", Severity), name="severity_valid"),
        CheckConstraint(_one_of("status", AnomalyStatus), name="status_valid"),
        CheckConstraint("confidence_score BETWEEN 0 AND 100", name="confidence_range"),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_by IS NULL AND resolved_at IS NULL)"
            " OR (status <> 'OPEN' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="resolution_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID]
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    anomaly_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(10))
    confidence_score: Mapped[Decimal] = mapped_column(Confidence)
    description: Mapped[str] = mapped_column(Text)
    detected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(12), default=AnomalyStatus.OPEN.value, server_default=AnomalyStatus.OPEN.value
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    invoice: Mapped[Invoice] = relationship(back_populates="anomalies", lazy="raise")


class AuditLog(Base):
    """The invoice's chain of custody: ingestion, then every reviewer decision, with its signer.

    Append-only. Triggers reject updates, and deletes other than removing the whole tenant. A
    deferred trigger on anomaly_logs refuses to commit a resolution that has no entry here, so a
    decision can't be recorded without its signer, note and the document's hash.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        # RESTRICT: an invoice with a custody record cannot be deleted.
        ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="RESTRICT",
            name="fk_audit_logs_invoice_id_organization_id_invoices",
        ),
        Index("ix_audit_logs_invoice_id_created_at", "invoice_id", "created_at"),
        Index("ix_audit_logs_anomaly_id", "anomaly_id"),
        Index("ix_audit_logs_organization_id_created_at", "organization_id", "created_at"),
        CheckConstraint(_one_of("action", AuditAction), name="action_valid"),
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="file_sha256_hex"),
        CheckConstraint(
            "previous_status IS NULL OR " + _one_of("previous_status", InvoiceStatus), name="previous_status_valid"
        ),
        CheckConstraint("new_status IS NULL OR " + _one_of("new_status", InvoiceStatus), name="new_status_valid"),
        # A reviewer decision always names its signer; only ingestion may come from an integration.
        CheckConstraint(
            "action = 'INGESTED' OR (user_id IS NOT NULL AND user_display_name IS NOT NULL)",
            name="review_signed",
        ),
        CheckConstraint(
            "(action IN ('ANOMALY_DISMISSED', 'ANOMALY_CONFIRMED')) = (anomaly_id IS NOT NULL)",
            name="anomaly_reference",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    invoice_id: Mapped[uuid.UUID]
    # The finding a resolution decided; empty for ingestion and approvals.
    anomaly_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("anomaly_logs.id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    # The signer's name as it was when they signed; later renames don't rewrite history.
    user_display_name: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(32))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    # Empty only on entries backfilled by migration 0005, which can't know past statuses.
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str | None] = mapped_column(String(20))
    file_sha256: Mapped[str] = mapped_column(CHAR(64))  # the invoice's document hash at the time
    details: Mapped[dict[str, Any]] = _jsonb_object()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TaxRateLimit(TimestampMixin, Base):
    """Highest tax rate considered normal for a country. Operator-maintained reference data."""

    __tablename__ = "tax_rate_limits"
    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_iso"),
        CheckConstraint("max_rate_percent >= 0 AND max_rate_percent <= 100", name="max_rate_range"),
    )

    country_code: Mapped[str] = mapped_column(CHAR(2), primary_key=True)
    max_rate_percent: Mapped[Decimal] = mapped_column(Money)
    note: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    """Uploaded original, stored in the database.

    The file system is not durable on serverless platforms (and read-only outside /tmp), so the
    bytes live here instead, content-addressed per tenant exactly like the local store.
    """

    __tablename__ = "documents"
    __table_args__ = (CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    sha256: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
