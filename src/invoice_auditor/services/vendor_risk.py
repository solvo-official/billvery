"""Vendor risk scorecards: each vendor's audit history, aggregated, with an explainable risk tier.

Metrics come from the whole ledger in one query, not from a cached copy, so a scorecard is
always consistent with the invoices and findings behind it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..enums import AnomalyStatus, AnomalyType, InvoiceStatus, Severity, VendorRiskTier
from ..models import AnomalyLog, Invoice, Vendor

DUPLICATE_TYPES = (
    AnomalyType.DUPLICATE_FILE_HASH.value,
    AnomalyType.DUPLICATE_INVOICE_NUMBER.value,
    AnomalyType.SIMILAR_INVOICE_NUMBER.value,
)
BLOCKING = tuple(severity.value for severity in Severity if severity.is_blocking)


@dataclass(frozen=True, slots=True)
class VendorMetrics:
    total_invoices: int
    #: sent to NEEDS_REVIEW by the rule engine (any HIGH/CRITICAL finding at ingestion)
    held_for_review: int
    #: flagged as a possible duplicate, whatever the reviewer decided
    duplicate_invoices: int
    #: a reviewer confirmed the duplicate finding
    confirmed_duplicates: int
    rejected_invoices: int
    in_review: int

    @property
    def flag_rate(self) -> float:
        return _percent(self.held_for_review, self.total_invoices)

    @property
    def duplicate_rate(self) -> float:
        return _percent(self.duplicate_invoices, self.total_invoices)


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    tier: VendorRiskTier
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VendorScorecard:
    vendor_id: UUID
    name: str
    tax_id: str | None
    first_seen_at: datetime
    last_invoice_at: datetime
    metrics: VendorMetrics
    risk: RiskAssessment


def _percent(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def assess(metrics: VendorMetrics, settings: Settings) -> RiskAssessment:
    """HIGH, MEDIUM or LOW, with the reasons that decided it (most serious first).

    HIGH: a rejected invoice or confirmed duplicate, or, with enough history, a flag rate or
    duplicate rate at the HIGH threshold. MEDIUM: a flag rate at the MEDIUM threshold, or any
    possible duplicate. A short history can't reach HIGH on rates alone: one flagged invoice out
    of one is 100%, but says little.
    """
    high: list[str] = []
    medium: list[str] = []
    established = metrics.total_invoices >= settings.vendor_risk_min_history

    if metrics.rejected_invoices:
        high.append(f"{_count(metrics.rejected_invoices, 'invoice')} rejected after a reviewer confirmed a blocking finding")
    if metrics.confirmed_duplicates:
        high.append(f"{_count(metrics.confirmed_duplicates, 'confirmed duplicate')}")

    flag_note = f"{metrics.flag_rate:g}% of invoices held for review ({metrics.held_for_review} of {metrics.total_invoices})"
    if established and metrics.flag_rate >= settings.vendor_risk_high_flag_rate:
        high.append(flag_note)
    elif metrics.flag_rate >= settings.vendor_risk_medium_flag_rate:
        medium.append(flag_note)

    unconfirmed = metrics.duplicate_invoices - metrics.confirmed_duplicates
    duplicate_note = f"{metrics.duplicate_rate:g}% flagged as possible duplicates ({metrics.duplicate_invoices} of {metrics.total_invoices})"
    if established and metrics.duplicate_rate >= settings.vendor_risk_high_duplicate_rate:
        high.append(duplicate_note)
    elif unconfirmed > 0:
        medium.append(f"{_count(unconfirmed, 'possible duplicate')} flagged")

    if high:
        tier, reasons = VendorRiskTier.HIGH, high + medium
    elif medium:
        tier, reasons = VendorRiskTier.MEDIUM, medium
    else:
        tier, reasons = VendorRiskTier.LOW, [f"{metrics.flag_rate:g}% of invoices held for review, no duplicates"]
    if not established:
        reasons.append(f"Limited history: {_count(metrics.total_invoices, 'invoice')}")
    return RiskAssessment(tier=tier, reasons=reasons)


async def vendor_scorecards(
    session: AsyncSession, organization_id: UUID, settings: Settings, *, vendor_ids: list[UUID] | None = None
) -> list[VendorScorecard]:
    """One scorecard per vendor with at least one invoice, riskiest first."""
    per_invoice = (
        select(
            AnomalyLog.invoice_id.label("invoice_id"),
            func.bool_or(AnomalyLog.severity.in_(BLOCKING)).label("held"),
            func.bool_or(AnomalyLog.anomaly_type.in_(DUPLICATE_TYPES)).label("duplicate"),
            func.bool_or(
                and_(AnomalyLog.anomaly_type.in_(DUPLICATE_TYPES), AnomalyLog.status == AnomalyStatus.CONFIRMED.value)
            ).label("confirmed_duplicate"),
        )
        .where(AnomalyLog.organization_id == organization_id)
        .group_by(AnomalyLog.invoice_id)
        .subquery()
    )
    stmt = (
        select(
            Vendor.id,
            Vendor.name,
            Vendor.tax_id,
            Vendor.created_at,
            func.count(Invoice.id).label("total"),
            func.count().filter(per_invoice.c.held).label("held"),
            func.count().filter(per_invoice.c.duplicate).label("duplicates"),
            func.count().filter(per_invoice.c.confirmed_duplicate).label("confirmed_duplicates"),
            func.count().filter(Invoice.status == InvoiceStatus.REJECTED.value).label("rejected"),
            func.count().filter(Invoice.status == InvoiceStatus.NEEDS_REVIEW.value).label("in_review"),
            func.max(Invoice.created_at).label("last_invoice_at"),
        )
        .join(Invoice, and_(Invoice.vendor_id == Vendor.id, Invoice.organization_id == Vendor.organization_id))
        .outerjoin(per_invoice, per_invoice.c.invoice_id == Invoice.id)
        .where(Vendor.organization_id == organization_id)
        .group_by(Vendor.id)
    )
    if vendor_ids is not None:
        stmt = stmt.where(Vendor.id.in_(vendor_ids))

    cards: list[VendorScorecard] = []
    for row in (await session.execute(stmt)).all():
        metrics = VendorMetrics(
            total_invoices=row.total,
            held_for_review=row.held,
            duplicate_invoices=row.duplicates,
            confirmed_duplicates=row.confirmed_duplicates,
            rejected_invoices=row.rejected,
            in_review=row.in_review,
        )
        cards.append(
            VendorScorecard(
                vendor_id=row.id,
                name=row.name,
                tax_id=row.tax_id,
                first_seen_at=row.created_at,
                last_invoice_at=row.last_invoice_at,
                metrics=metrics,
                risk=assess(metrics, settings),
            )
        )
    rank = {VendorRiskTier.HIGH: 0, VendorRiskTier.MEDIUM: 1, VendorRiskTier.LOW: 2}
    cards.sort(key=lambda card: (rank[card.risk.tier], -card.metrics.flag_rate, -card.metrics.total_invoices, card.name.lower()))
    return cards
