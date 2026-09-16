"""Anomaly rule engine.

Every rule yields `Finding`s; the processor stores each one as an immutable anomaly_logs row.
Pure `evaluate_*` functions hold the arithmetic; `AnomalyDetectionService` adds the checks
that need history from the database. All queries are scoped to one organization.
"""

import difflib
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..enums import AnomalyStatus, AnomalyType, InvoiceStatus, Severity
from ..models import Invoice, InvoiceItem, Organization, TaxRateLimit, Vendor
from ..money import format_amount, quantize
from .normalization import normalize_tax_id, normalize_vendor_name

logger = logging.getLogger(__name__)

HUNDRED = Decimal(100)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _pct(value: Decimal) -> str:
    """90.00 -> '90', 89.50 -> '89.5'."""
    text = f"{value.quantize(Decimal('0.01')):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True, slots=True)
class Finding:
    anomaly_type: AnomalyType
    severity: Severity
    confidence: Decimal  # 0-100: how likely the finding is a real problem
    description: str
    detected_value: dict[str, Any] | None = None  # JSON-safe evidence
    expected_value: dict[str, Any] | None = None  # JSON-safe expectation


@dataclass(frozen=True, slots=True)
class SimilarVendor:
    id: UUID
    name: str
    similarity: float


@dataclass(slots=True)
class VendorResolution:
    vendor: Vendor | None
    similar_vendors: list[SimilarVendor] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def vendor_ids(self) -> list[UUID]:
        """The resolved vendor plus its look-alikes; duplicate checks search all of them."""
        ids = [self.vendor.id] if self.vendor is not None else []
        return ids + [similar.id for similar in self.similar_vendors if similar.id not in ids]


@dataclass(frozen=True, slots=True)
class PriceHistory:
    average_unit_price: Decimal
    samples: int


@dataclass(frozen=True, slots=True)
class _FileDuplicates:
    finding: Finding
    invoice_ids: frozenset[UUID]


# --- Pure rules -----------------------------------------------------------------------------


def evaluate_missing_fields(
    *, vendor_name: str | None, invoice_number: str | None, invoice_date: date | None, total_amount: Decimal | None
) -> Finding | None:
    fields = {
        "vendor_name": vendor_name,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "total_amount": total_amount,
    }
    missing = [name for name, value in fields.items() if value is None]
    if not missing:
        return None
    return Finding(
        AnomalyType.MISSING_REQUIRED_FIELDS,
        Severity.MEDIUM,
        Decimal(100),
        f"Required field(s) missing from the extraction: {', '.join(missing)}.",
        detected_value={"missing_fields": missing},
    )


def evaluate_extraction_confidence(
    confidence: Decimal, *, approve_at: Decimal, critical_below: Decimal
) -> Finding | None:
    """Below `approve_at` the invoice needs a human; below `critical_below` the read is likely unusable."""
    if confidence >= approve_at:
        return None
    severity = Severity.CRITICAL if confidence < critical_below else Severity.HIGH
    return Finding(
        AnomalyType.LOW_EXTRACTION_CONFIDENCE,
        severity,
        Decimal(100),
        f"AI extraction confidence is {_pct(confidence)}%, below the {_pct(approve_at)}% auto-approval threshold.",
        detected_value={"confidence": float(confidence)},
        expected_value={"minimum_confidence": float(approve_at)},
    )


def evaluate_invoice_date(
    invoice_date: date | None, *, today: date, stale_after_days: int, future_grace_days: int
) -> Finding | None:
    if invoice_date is None:
        return None
    latest_allowed = today + timedelta(days=future_grace_days)  # grace absorbs time-zone differences
    if invoice_date > latest_allowed:
        return Finding(
            AnomalyType.FUTURE_INVOICE_DATE,
            Severity.HIGH,
            Decimal(95),
            f"Invoice is dated {invoice_date.isoformat()}, which is in the future.",
            detected_value={"invoice_date": invoice_date.isoformat()},
            expected_value={"latest_allowed_date": latest_allowed.isoformat()},
        )
    earliest_expected = today - timedelta(days=stale_after_days)
    if invoice_date < earliest_expected:
        return Finding(
            AnomalyType.STALE_INVOICE_DATE,
            Severity.MEDIUM,
            Decimal(90),
            f"Invoice is dated {invoice_date.isoformat()}, more than {stale_after_days} days ago.",
            detected_value={"invoice_date": invoice_date.isoformat()},
            expected_value={"earliest_expected_date": earliest_expected.isoformat()},
        )
    return None


def evaluate_math(
    *,
    subtotal: Decimal | None,
    tax_amount: Decimal,
    shipping_amount: Decimal,
    discount_amount: Decimal,
    total_amount: Decimal | None,
    tolerance: Decimal,
) -> Finding | None:
    """Flags abs((subtotal + tax + shipping - discount) - total) > tolerance.

    Severity by the difference relative to the total: under 1% LOW, 1-5% MEDIUM, over 5% HIGH.
    """
    if subtotal is None or total_amount is None:
        return None  # a missing total is reported by the missing-fields rule
    expected = subtotal + tax_amount + shipping_amount - discount_amount
    difference = abs(expected - total_amount)
    if difference <= tolerance:
        return None
    if total_amount == 0:
        percent = None
        severity = Severity.HIGH
    else:
        percent = difference / abs(total_amount) * HUNDRED
        severity = Severity.LOW if percent < 1 else Severity.MEDIUM if percent <= 5 else Severity.HIGH
    share = f", {percent:.2f}% of the total" if percent is not None else ""
    return Finding(
        AnomalyType.MATH_TOTAL_MISMATCH,
        severity,
        Decimal(95),
        f"Total {format_amount(total_amount)} does not equal subtotal + tax + shipping - discount "
        f"= {format_amount(expected)} (off by {format_amount(difference)}{share}).",
        detected_value={
            "total_amount": format_amount(total_amount),
            "difference": format_amount(difference),
            "difference_percent": float(round(percent, 4)) if percent is not None else None,
        },
        expected_value={
            "total_amount": format_amount(expected),
            "formula": "subtotal + tax + shipping - discount",
            "tolerance": format_amount(tolerance),
        },
    )


def evaluate_tax_rate(
    *,
    subtotal: Decimal | None,
    tax_amount: Decimal,
    max_rate_percent: Decimal,
    limit_source: str,
    tolerance: Decimal,
) -> Finding | None:
    """Flags tax above subtotal x limit by more than `tolerance` (absorbs per-line rounding)."""
    if subtotal is None or subtotal <= 0 or tax_amount <= 0:
        return None
    max_tax = subtotal * max_rate_percent / HUNDRED
    if tax_amount - max_tax <= tolerance:
        return None
    effective_rate = tax_amount / subtotal * HUNDRED
    return Finding(
        AnomalyType.TAX_RATE_EXCEEDED,
        Severity.HIGH,
        Decimal(90),
        f"Tax of {format_amount(tax_amount)} is {effective_rate:.2f}% of the subtotal, above the "
        f"{_pct(max_rate_percent)}% limit ({limit_source}).",
        detected_value={
            "tax_amount": format_amount(tax_amount),
            "effective_rate_percent": float(round(effective_rate, 4)),
        },
        expected_value={
            "max_rate_percent": float(max_rate_percent),
            "max_tax_amount": format_amount(quantize(max_tax)),
            "limit_source": limit_source,
        },
    )


def evaluate_price_spikes(
    items: Sequence[InvoiceItem],
    history: Mapping[str, PriceHistory],
    *,
    ratio: Decimal,
    min_samples: int,
    currency: str,
) -> list[Finding]:
    """Flags a line whose unit price is at least `ratio` x its historical average."""
    findings: list[Finding] = []
    flagged: set[str] = set()
    for item in items:
        key = item.description_normalized
        if not key or key in flagged or item.unit_price <= 0:
            continue
        stats = history.get(key)
        if stats is None or stats.samples < min_samples or stats.average_unit_price <= 0:
            continue
        current_ratio = item.unit_price / stats.average_unit_price
        if current_ratio < ratio:
            continue
        flagged.add(key)
        findings.append(
            Finding(
                AnomalyType.PRICE_SPIKE,
                Severity.HIGH,
                min(Decimal(95), Decimal(50) + Decimal(10) * stats.samples),  # more history, more certainty
                f"'{item.description}' costs {format_amount(item.unit_price)} {currency} per unit, "
                f"{current_ratio:.1f}x the historical average of {format_amount(stats.average_unit_price)} "
                f"across {stats.samples} earlier purchases.",
                detected_value={
                    "line_number": item.line_number,
                    "description": item.description,
                    "unit_price": format_amount(item.unit_price),
                    "ratio": float(round(current_ratio, 2)),
                },
                expected_value={
                    "historical_average_unit_price": format_amount(stats.average_unit_price),
                    "samples": stats.samples,
                    "spike_ratio_threshold": float(ratio),
                    "currency": currency,
                },
            )
        )
    return findings


def is_similar_invoice_number(a: str, b: str, *, min_ratio: float) -> bool:
    """Near-identical normalized numbers, e.g. OCR slips like "10022" vs "10O22"."""
    return a != b and difflib.SequenceMatcher(None, a, b).ratio() >= min_ratio


def decide_invoice_status(anomalies: Iterable[tuple[str, str]]) -> InvoiceStatus:
    """Derive the invoice status from its (severity, status) anomaly pairs.

    Any confirmed HIGH/CRITICAL finding rejects the invoice; any open one holds it for review;
    otherwise it is approved. Low extraction confidence is itself a HIGH/CRITICAL finding, so
    "AI confidence < 90 -> NEEDS_REVIEW" follows from the same rule.
    """
    blocking = [AnomalyStatus(status) for severity, status in anomalies if Severity(severity).is_blocking]
    if AnomalyStatus.CONFIRMED in blocking:
        return InvoiceStatus.REJECTED
    if AnomalyStatus.OPEN in blocking:
        return InvoiceStatus.NEEDS_REVIEW
    return InvoiceStatus.APPROVED


# --- Database-backed checks ---------------------------------------------------------------


class AnomalyDetectionService:
    """Runs every rule for one invoice. Must be called inside the caller's transaction, after
    `lock_organization`, so that check-then-insert cannot race with a concurrent submission."""

    def __init__(self, session: AsyncSession, settings: Settings, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.session = session
        self.settings = settings
        self.clock = clock

    # Vendor lookup / normalization ------------------------------------------------------

    async def resolve_vendor(
        self, organization_id: UUID, vendor_name: str | None, vendor_tax_id: str | None
    ) -> VendorResolution:
        """Find or create the vendor record, reporting identity conflicts along the way.

        Tax ID is the stronger identifier and is tried first; the normalized name second.
        Unknown vendors are created, never merged into a look-alike -- a look-alike is reported
        instead and still included in duplicate-invoice searches.
        """
        name_key = normalize_vendor_name(vendor_name)
        tax_key = normalize_tax_id(vendor_tax_id)
        findings: list[Finding] = []
        vendor: Vendor | None = None

        if tax_key:
            vendor = await self.session.scalar(
                select(Vendor).where(Vendor.organization_id == organization_id, Vendor.tax_id == tax_key)
            )
            if vendor is not None and name_key and vendor.normalized_name != name_key:
                similarity = await self._similarity(vendor.normalized_name, name_key)
                findings.append(
                    Finding(
                        AnomalyType.VENDOR_NAME_MISMATCH,
                        Severity.MEDIUM,
                        Decimal(80),
                        f"Tax ID {tax_key} is registered to vendor '{vendor.name}', "
                        f"but this invoice names '{vendor_name}'.",
                        detected_value={"vendor_name": vendor_name, "vendor_tax_id": tax_key},
                        expected_value={
                            "vendor_id": str(vendor.id),
                            "vendor_name": vendor.name,
                            "name_similarity": round(similarity, 3),
                        },
                    )
                )

        if vendor is None and name_key:
            vendor = await self.session.scalar(
                select(Vendor).where(Vendor.organization_id == organization_id, Vendor.normalized_name == name_key)
            )
            if vendor is not None and tax_key:
                if vendor.tax_id is None:
                    vendor.tax_id = tax_key  # first time this vendor's tax ID has been seen
                elif vendor.tax_id != tax_key:
                    findings.append(
                        Finding(
                            AnomalyType.VENDOR_TAX_ID_MISMATCH,
                            Severity.HIGH,
                            Decimal(90),
                            f"Vendor '{vendor.name}' is on file with tax ID {vendor.tax_id}, "
                            f"but this invoice shows {tax_key}.",
                            detected_value={"vendor_name": vendor_name, "vendor_tax_id": tax_key},
                            expected_value={"vendor_id": str(vendor.id), "vendor_tax_id": vendor.tax_id},
                        )
                    )

        similar: list[SimilarVendor] = []
        if name_key:
            similar = await self._similar_vendors(
                organization_id, name_key, exclude_id=vendor.id if vendor is not None else None
            )

        if vendor is None and name_key:
            vendor = Vendor(
                organization_id=organization_id,
                name=(vendor_name or name_key).strip()[:255],
                normalized_name=name_key,
                tax_id=tax_key,
            )
            self.session.add(vendor)
            await self.session.flush()
            if similar:
                best = similar[0]
                findings.append(
                    Finding(
                        AnomalyType.SIMILAR_VENDOR_NAME,
                        Severity.MEDIUM,
                        Decimal(str(round(best.similarity * 100, 2))),
                        f"New vendor '{vendor_name}' closely resembles existing vendor '{best.name}' "
                        f"({best.similarity:.0%} similar).",
                        detected_value={"vendor_name": vendor_name, "normalized_name": name_key},
                        expected_value={
                            "similar_vendors": [
                                {"vendor_id": str(v.id), "name": v.name, "similarity": round(v.similarity, 3)}
                                for v in similar
                            ]
                        },
                    )
                )

        return VendorResolution(vendor=vendor, similar_vendors=similar, findings=findings)

    async def _similarity(self, a: str, b: str) -> float:
        return float(await self.session.scalar(select(func.similarity(a, b))))

    async def _similar_vendors(
        self, organization_id: UUID, name_key: str, *, exclude_id: UUID | None
    ) -> list[SimilarVendor]:
        # `%` uses pg_trgm.similarity_threshold and can use the GIN trigram index;
        # set_config(..., true) scopes the threshold to this transaction.
        await self.session.execute(
            select(func.set_config("pg_trgm.similarity_threshold", str(self.settings.vendor_similarity_threshold), True))
        )
        score = func.similarity(Vendor.normalized_name, name_key)
        stmt = (
            select(Vendor.id, Vendor.name, score.label("score"))
            .where(Vendor.organization_id == organization_id, Vendor.normalized_name.op("%")(name_key))
            .order_by(score.desc(), Vendor.created_at)
            .limit(5)
        )
        if exclude_id is not None:
            stmt = stmt.where(Vendor.id != exclude_id)
        rows = (await self.session.execute(stmt)).all()
        return [SimilarVendor(id=row.id, name=row.name, similarity=float(row.score)) for row in rows]

    # All rules --------------------------------------------------------------------------

    async def detect(
        self,
        invoice: Invoice,
        items: Sequence[InvoiceItem],
        organization: Organization,
        vendors: VendorResolution,
    ) -> list[Finding]:
        """Run every rule against an invoice that has not been inserted yet. Most severe first."""
        settings = self.settings
        now = self.clock()
        findings: list[Finding] = list(vendors.findings)

        for finding in (
            evaluate_missing_fields(
                vendor_name=invoice.vendor_name,
                invoice_number=invoice.invoice_number,
                invoice_date=invoice.invoice_date,
                total_amount=invoice.total_amount,
            ),
            evaluate_extraction_confidence(
                invoice.ai_confidence,
                approve_at=settings.auto_approve_confidence,
                critical_below=settings.critical_confidence,
            ),
            evaluate_invoice_date(
                invoice.invoice_date,
                today=now.date(),
                stale_after_days=settings.stale_invoice_days,
                future_grace_days=settings.future_date_grace_days,
            ),
            evaluate_math(
                subtotal=invoice.subtotal,
                tax_amount=invoice.tax_amount,
                shipping_amount=invoice.shipping_amount,
                discount_amount=invoice.discount_amount,
                total_amount=invoice.total_amount,
                tolerance=settings.math_tolerance,
            ),
        ):
            if finding is not None:
                findings.append(finding)

        file_duplicates = await self._check_duplicate_file(invoice)
        if file_duplicates is not None:
            findings.append(file_duplicates.finding)
        findings.extend(
            await self._check_duplicate_invoice_number(
                invoice,
                vendors.vendor_ids,
                already_matched=file_duplicates.invoice_ids if file_duplicates else frozenset(),
                now=now,
            )
        )
        findings.extend(await self._check_price_spikes(invoice, items, now=now))
        tax_finding = await self._check_tax_rate(invoice, organization)
        if tax_finding is not None:
            findings.append(tax_finding)

        findings.sort(key=lambda f: -f.severity.rank)
        return findings

    # Duplicate file hash ----------------------------------------------------------------

    async def _check_duplicate_file(self, invoice: Invoice) -> _FileDuplicates | None:
        rows = (
            await self.session.execute(
                select(Invoice.id, Invoice.invoice_number, Invoice.created_at)
                .where(
                    Invoice.organization_id == invoice.organization_id,  # never compare across tenants
                    Invoice.file_hash == invoice.file_hash,
                    Invoice.id != invoice.id,
                )
                .order_by(Invoice.created_at)
                .limit(20)
            )
        ).all()
        if not rows:
            return None
        finding = Finding(
            AnomalyType.DUPLICATE_FILE_HASH,
            Severity.CRITICAL,
            Decimal(100),
            f"This exact file was already submitted {len(rows)} time(s), first as invoice {rows[0].id} "
            f"on {rows[0].created_at.date().isoformat()}.",
            detected_value={
                "file_hash": invoice.file_hash,
                "duplicate_of": [
                    {"invoice_id": str(r.id), "invoice_number": r.invoice_number, "submitted_at": r.created_at.isoformat()}
                    for r in rows
                ],
            },
        )
        return _FileDuplicates(finding, frozenset(r.id for r in rows))

    # Duplicate / similar invoice number -------------------------------------------------

    async def _check_duplicate_invoice_number(
        self,
        invoice: Invoice,
        vendor_ids: list[UUID],
        *,
        already_matched: frozenset[UUID],
        now: datetime,
    ) -> list[Finding]:
        number = invoice.invoice_number_normalized
        if not number or not vendor_ids:
            return []
        window = self.settings.duplicate_window_days
        in_scope = (
            Invoice.organization_id == invoice.organization_id,
            Invoice.vendor_id.in_(vendor_ids),
            Invoice.created_at >= now - timedelta(days=window),
            Invoice.id != invoice.id,
        )
        findings: list[Finding] = []

        exact = (
            await self.session.execute(
                select(Invoice.id, Invoice.invoice_number, Invoice.vendor_name, Invoice.created_at)
                .where(*in_scope, Invoice.invoice_number_normalized == number)
                .order_by(Invoice.created_at)
                .limit(20)
            )
        ).all()
        # A byte-identical file is already reported as DUPLICATE_FILE_HASH; don't report it twice.
        exact = [row for row in exact if row.id not in already_matched]
        if exact:
            findings.append(
                Finding(
                    AnomalyType.DUPLICATE_INVOICE_NUMBER,
                    Severity.HIGH,
                    Decimal(95),
                    f"Invoice number {invoice.invoice_number} from this vendor was already submitted within "
                    f"the last {window} days (first as invoice {exact[0].id}).",
                    detected_value={
                        "invoice_number": invoice.invoice_number,
                        "matches": [
                            {
                                "invoice_id": str(r.id),
                                "invoice_number": r.invoice_number,
                                "vendor_name": r.vendor_name,
                                "submitted_at": r.created_at.isoformat(),
                            }
                            for r in exact
                        ],
                    },
                    expected_value={"window_days": window},
                )
            )

        # Same vendor, date, currency and total with an almost-identical number: likely a re-keyed
        # or OCR-mangled resubmission. Date and total are required so that sequential invoices
        # from the same vendor (INV-1002, INV-1003, ...) are not flagged.
        if invoice.invoice_date is not None and invoice.total_amount is not None:
            candidates = (
                await self.session.execute(
                    select(Invoice.id, Invoice.invoice_number, Invoice.invoice_number_normalized)
                    .where(
                        *in_scope,
                        Invoice.invoice_number_normalized.is_not(None),
                        Invoice.invoice_number_normalized != number,
                        Invoice.invoice_date == invoice.invoice_date,
                        Invoice.currency == invoice.currency,
                        Invoice.total_amount == invoice.total_amount,
                    )
                    .limit(50)
                )
            ).all()
            similar = [
                row
                for row in candidates
                if row.id not in already_matched
                and is_similar_invoice_number(
                    number, row.invoice_number_normalized, min_ratio=self.settings.similar_invoice_number_ratio
                )
            ]
            if similar:
                findings.append(
                    Finding(
                        AnomalyType.SIMILAR_INVOICE_NUMBER,
                        Severity.MEDIUM,
                        Decimal(70),
                        f"Invoice number {invoice.invoice_number} closely resembles {similar[0].invoice_number}, "
                        f"submitted by the same vendor with the same date and total.",
                        detected_value={
                            "invoice_number": invoice.invoice_number,
                            "matches": [{"invoice_id": str(r.id), "invoice_number": r.invoice_number} for r in similar],
                        },
                    )
                )
        return findings

    # Price spike ------------------------------------------------------------------------

    async def _check_price_spikes(
        self, invoice: Invoice, items: Sequence[InvoiceItem], *, now: datetime
    ) -> list[Finding]:
        keys = {item.description_normalized for item in items if item.description_normalized and item.unit_price > 0}
        if not keys:
            return []
        # Baseline: this organization's earlier purchases of the same item in the same currency,
        # excluding rejected invoices (their prices are not trustworthy).
        rows = (
            await self.session.execute(
                select(InvoiceItem.description_normalized, func.avg(InvoiceItem.unit_price), func.count())
                .join(
                    Invoice,
                    and_(Invoice.id == InvoiceItem.invoice_id, Invoice.organization_id == InvoiceItem.organization_id),
                )
                .where(
                    InvoiceItem.organization_id == invoice.organization_id,
                    InvoiceItem.description_normalized.in_(keys),
                    InvoiceItem.unit_price > 0,
                    Invoice.currency == invoice.currency,
                    Invoice.status != InvoiceStatus.REJECTED.value,
                    Invoice.id != invoice.id,
                    Invoice.created_at >= now - timedelta(days=self.settings.price_history_days),
                )
                .group_by(InvoiceItem.description_normalized)
            )
        ).all()
        history = {key: PriceHistory(quantize(average), count) for key, average, count in rows}
        return evaluate_price_spikes(
            items,
            history,
            ratio=self.settings.price_spike_ratio,
            min_samples=self.settings.price_min_samples,
            currency=invoice.currency,
        )

    # Tax limit --------------------------------------------------------------------------

    async def _check_tax_rate(self, invoice: Invoice, organization: Organization) -> Finding | None:
        limit = await self._tax_rate_limit(organization)
        if limit is None:
            return None
        max_rate, source = limit
        return evaluate_tax_rate(
            subtotal=invoice.subtotal,
            tax_amount=invoice.tax_amount,
            max_rate_percent=max_rate,
            limit_source=source,
            tolerance=self.settings.tax_tolerance,
        )

    async def _tax_rate_limit(self, organization: Organization) -> tuple[Decimal, str] | None:
        """Tenant override (settings.max_tax_rate_percent) first, then the country table."""
        override = organization.settings.get("max_tax_rate_percent")
        if override is not None:
            try:
                return quantize(Decimal(str(override))), "organization setting"
            except (InvalidOperation, ValueError):
                logger.warning(
                    "ignoring invalid max_tax_rate_percent %r for organization %s", override, organization.id
                )
        if organization.country_code:
            limit = await self.session.get(TaxRateLimit, organization.country_code)
            if limit is not None:
                return limit.max_rate_percent, f"{organization.country_code} country limit"
        return None
