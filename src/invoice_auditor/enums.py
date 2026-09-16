from enum import StrEnum


class InvoiceStatus(StrEnum):
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def is_blocking(self) -> bool:
        """HIGH and CRITICAL findings hold an invoice for human review."""
        return self in (Severity.HIGH, Severity.CRITICAL)


_SEVERITY_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


class AnomalyStatus(StrEnum):
    OPEN = "OPEN"
    DISMISSED = "DISMISSED"  # reviewed: false positive or accepted
    CONFIRMED = "CONFIRMED"  # reviewed: the problem is real


class AnomalyType(StrEnum):
    DUPLICATE_FILE_HASH = "DUPLICATE_FILE_HASH"
    DUPLICATE_INVOICE_NUMBER = "DUPLICATE_INVOICE_NUMBER"
    SIMILAR_INVOICE_NUMBER = "SIMILAR_INVOICE_NUMBER"
    VENDOR_TAX_ID_MISMATCH = "VENDOR_TAX_ID_MISMATCH"
    VENDOR_NAME_MISMATCH = "VENDOR_NAME_MISMATCH"
    SIMILAR_VENDOR_NAME = "SIMILAR_VENDOR_NAME"
    MATH_TOTAL_MISMATCH = "MATH_TOTAL_MISMATCH"
    PRICE_SPIKE = "PRICE_SPIKE"
    FUTURE_INVOICE_DATE = "FUTURE_INVOICE_DATE"
    STALE_INVOICE_DATE = "STALE_INVOICE_DATE"
    MISSING_REQUIRED_FIELDS = "MISSING_REQUIRED_FIELDS"
    TAX_RATE_EXCEEDED = "TAX_RATE_EXCEEDED"
    LOW_EXTRACTION_CONFIDENCE = "LOW_EXTRACTION_CONFIDENCE"


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    REVIEWER = "reviewer"
    MEMBER = "member"

    @property
    def can_resolve_anomalies(self) -> bool:
        return self is not UserRole.MEMBER


class ApiScope(StrEnum):
    INVOICES_WRITE = "invoices:write"
    INVOICES_READ = "invoices:read"
    ANOMALIES_RESOLVE = "anomalies:resolve"
