from datetime import date, timedelta
from decimal import Decimal

import pytest

from invoice_auditor.enums import AnomalyStatus, AnomalyType, InvoiceStatus, Severity
from invoice_auditor.models import InvoiceItem
from invoice_auditor.money import format_amount, quantize
from invoice_auditor.services.anomaly_detection import (
    PriceHistory,
    decide_invoice_status,
    evaluate_extraction_confidence,
    evaluate_invoice_date,
    evaluate_math,
    evaluate_missing_fields,
    evaluate_price_spikes,
    evaluate_tax_rate,
    is_similar_invoice_number,
)

D = Decimal


def math(subtotal="1000", tax="50", shipping="0", discount="0", total="1050"):
    return evaluate_math(
        subtotal=D(subtotal) if subtotal is not None else None,
        tax_amount=D(tax),
        shipping_amount=D(shipping),
        discount_amount=D(discount),
        total_amount=D(total) if total is not None else None,
        tolerance=D("0.05"),
    )


class TestMath:
    def test_balanced_invoice_passes(self):
        assert math() is None
        assert math(shipping="25", discount="75", total="1000") is None

    def test_tolerance_is_inclusive(self):
        assert math(total="1050.05") is None
        assert math(total="1049.95") is None

    @pytest.mark.parametrize(
        ("subtotal", "total", "severity"),
        [
            ("1000", "1050.06", Severity.LOW),  # 0.06 off 1050.06: 0.006%
            ("1000", "1060.50", Severity.LOW),  # 10.50 off 1060.50: 0.99%
            ("960", "1000", Severity.MEDIUM),  # expected 1010, 10 off 1000: exactly 1%
            ("1000", "1100", Severity.MEDIUM),  # 50 off 1100: 4.5%
            ("1000", "1000", Severity.MEDIUM),  # 50 off 1000: exactly 5%
            ("1000", "990", Severity.HIGH),  # 60 off 990: 6.1%
        ],
    )
    def test_severity_by_relative_difference(self, subtotal, total, severity):
        finding = math(subtotal=subtotal, total=total)
        assert finding.anomaly_type is AnomalyType.MATH_TOTAL_MISMATCH
        assert finding.severity is severity

    def test_zero_total_is_high(self):
        assert math(total="0").severity is Severity.HIGH

    def test_skipped_without_subtotal_or_total(self):
        assert math(subtotal=None) is None
        assert math(total=None) is None

    def test_finding_values_are_json_safe_strings(self):
        finding = math(total="1100")
        assert finding.detected_value["difference"] == "50.00"
        assert finding.expected_value["total_amount"] == "1050.00"


class TestConfidence:
    def run(self, value):
        return evaluate_extraction_confidence(D(value), approve_at=D(90), critical_below=D(70))

    def test_at_threshold_is_approved(self):
        assert self.run("90") is None
        assert self.run("99.5") is None

    def test_below_threshold_is_high(self):
        assert self.run("89.99").severity is Severity.HIGH
        assert self.run("70").severity is Severity.HIGH

    def test_very_low_is_critical(self):
        finding = self.run("69.99")
        assert finding.severity is Severity.CRITICAL
        assert finding.anomaly_type is AnomalyType.LOW_EXTRACTION_CONFIDENCE


class TestInvoiceDate:
    today = date(2026, 9, 15)

    def run(self, invoice_date):
        return evaluate_invoice_date(invoice_date, today=self.today, stale_after_days=365, future_grace_days=1)

    def test_normal_dates(self):
        assert self.run(self.today) is None
        assert self.run(self.today + timedelta(days=1)) is None  # grace for time zones
        assert self.run(self.today - timedelta(days=365)) is None
        assert self.run(None) is None

    def test_future(self):
        finding = self.run(self.today + timedelta(days=2))
        assert (finding.anomaly_type, finding.severity) == (AnomalyType.FUTURE_INVOICE_DATE, Severity.HIGH)

    def test_stale(self):
        finding = self.run(self.today - timedelta(days=366))
        assert (finding.anomaly_type, finding.severity) == (AnomalyType.STALE_INVOICE_DATE, Severity.MEDIUM)


def test_missing_fields():
    assert (
        evaluate_missing_fields(vendor_name="A", invoice_number="1", invoice_date=date.today(), total_amount=D(1))
        is None
    )
    finding = evaluate_missing_fields(vendor_name=None, invoice_number="1", invoice_date=None, total_amount=D(1))
    assert finding.severity is Severity.MEDIUM
    assert finding.detected_value == {"missing_fields": ["vendor_name", "invoice_date"]}


class TestTaxRate:
    def run(self, subtotal, tax, limit="5"):
        return evaluate_tax_rate(
            subtotal=D(subtotal) if subtotal is not None else None,
            tax_amount=D(tax),
            max_rate_percent=D(limit),
            limit_source="AE country limit",
            tolerance=D("0.05"),
        )

    def test_within_limit(self):
        assert self.run("1000", "50") is None
        assert self.run("1000", "50.05") is None  # rounding tolerance
        assert self.run("0.99", "0.05") is None  # 5.05% but only 0.0005 over: rounding

    def test_over_limit(self):
        finding = self.run("1000", "100")
        assert (finding.anomaly_type, finding.severity) == (AnomalyType.TAX_RATE_EXCEEDED, Severity.HIGH)
        assert finding.detected_value["effective_rate_percent"] == 10.0
        assert finding.expected_value["max_tax_amount"] == "50.00"

    def test_skipped_without_positive_subtotal_or_tax(self):
        assert self.run(None, "100") is None
        assert self.run("0", "100") is None
        assert self.run("1000", "0") is None


def item(line_number, description, unit_price):
    return InvoiceItem(
        line_number=line_number,
        description=description,
        description_normalized=description.lower(),
        quantity=D(1),
        unit_price=D(unit_price),
        line_total=D(unit_price),
    )


class TestPriceSpikes:
    history = {"printer cartridge": PriceHistory(D("50.0000"), 4)}

    def run(self, items, history=None, min_samples=3):
        return evaluate_price_spikes(
            items, history or self.history, ratio=D("2.5"), min_samples=min_samples, currency="USD"
        )

    def test_at_ratio_is_flagged(self):
        [finding] = self.run([item(1, "Printer cartridge", "125")])
        assert (finding.anomaly_type, finding.severity) == (AnomalyType.PRICE_SPIKE, Severity.HIGH)
        assert finding.detected_value["ratio"] == 2.5
        assert finding.confidence == D(90)

    def test_below_ratio_passes(self):
        assert self.run([item(1, "Printer cartridge", "124.99")]) == []

    def test_needs_enough_history(self):
        assert self.run([item(1, "Printer cartridge", "400")], min_samples=5) == []
        assert self.run([item(1, "Stapler", "400")]) == []

    def test_one_finding_per_product(self):
        findings = self.run([item(1, "Printer cartridge", "400"), item(2, "Printer cartridge", "500")])
        assert len(findings) == 1


def test_similar_invoice_numbers():
    assert is_similar_invoice_number("INV10022", "INV1O022", min_ratio=0.8)
    assert is_similar_invoice_number("INV10022", "INV10022A", min_ratio=0.8)
    assert not is_similar_invoice_number("INV10022", "INV10022", min_ratio=0.8)
    assert not is_similar_invoice_number("INV10022", "PO77", min_ratio=0.8)


@pytest.mark.parametrize(
    ("anomalies", "expected"),
    [
        ([], InvoiceStatus.APPROVED),
        ([("LOW", "OPEN"), ("MEDIUM", "OPEN")], InvoiceStatus.APPROVED),
        ([("HIGH", "OPEN")], InvoiceStatus.NEEDS_REVIEW),
        ([("CRITICAL", "OPEN"), ("HIGH", "DISMISSED")], InvoiceStatus.NEEDS_REVIEW),
        ([("HIGH", "DISMISSED"), ("CRITICAL", "DISMISSED")], InvoiceStatus.APPROVED),
        ([("HIGH", "CONFIRMED"), ("HIGH", "OPEN")], InvoiceStatus.REJECTED),
        ([("MEDIUM", "CONFIRMED")], InvoiceStatus.APPROVED),  # confirmed but not blocking
    ],
)
def test_decide_invoice_status(anomalies, expected):
    assert decide_invoice_status(anomalies) is expected
    assert decide_invoice_status((Severity(s), AnomalyStatus(st)) for s, st in anomalies) is expected


def test_money_helpers():
    assert quantize(D("12.34565")) == D("12.3457")
    assert quantize(D("-0.00001")) == D("0.0000") and not quantize(D("-0.00001")).is_signed()
    assert format_amount(D("1260")) == "1260.00"
    assert format_amount(D("0.0125")) == "0.0125"
    assert format_amount(D("-12.5")) == "-12.50"
    with pytest.raises(ValueError):
        quantize(D("100000000000000"))
    with pytest.raises(ValueError):
        quantize(D("NaN"))
