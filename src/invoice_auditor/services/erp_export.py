"""Batch exports of audited invoices in formats accounting systems import.

accounting_csv: the flat bill-import layout QuickBooks Online and Xero accept, one row per line
item with the invoice's header fields repeated on each row (importers group rows by
InvoiceNumber). An invoice without line items becomes one row for its net amount.

erp_json: a batch envelope of vendor-bill documents for SAP S/4HANA (A_SupplierInvoice) and
NetSuite (vendorBill) integrations: control totals per currency, supplier, dates, amounts, lines,
approval and the document's SHA-256. Field names are system-neutral; the README maps each one to
both systems. Company code, subsidiary and GL accounts belong to the integration's configuration.

Money is always an exact decimal string, never a float.
"""

import csv
import io
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from ..models import Organization
from ..money import ZERO, format_amount
from ..schemas.api import InvoiceAuditResponse

ACCOUNTING_CSV_COLUMNS = (
    "InvoiceNumber",
    "VendorName",
    "IssueDate",
    "DueDate",
    "LineItemDescription",
    "LineItemQuantity",
    "LineItemUnitPrice",
    "LineItemAmount",
    "TaxAmount",
    "TotalAmount",
    "Currency",
)
ERP_FORMAT = "billvery.ap-batch/v1"

# Text from uploaded documents is untrusted: a cell starting with one of these would run as a
# formula when the file is opened in a spreadsheet, so it gets a leading apostrophe.
_FORMULA_START = re.compile(r"^[=+\-@\t\r]")


def _text(value: str | None) -> str:
    if not value:
        return ""
    return f"'{value}" if _FORMULA_START.match(value) else value


def _amount(value: Decimal | None) -> str:
    return format_amount(value) if value is not None else ""


def _quantity(value: Decimal) -> str:
    return f"{value.normalize():f}"  # 2.0000 -> "2", 1.5000 -> "1.5", 100 -> "100"


def _day(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def accounting_csv(invoices: Sequence[InvoiceAuditResponse]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(ACCOUNTING_CSV_COLUMNS)
    for invoice in invoices:
        money = invoice.financial_summary
        header = (_text(invoice.invoice_number), _text(invoice.vendor.name), _day(invoice.invoice_date), _day(invoice.due_date))
        footer = (_amount(money.tax), _amount(money.total), money.currency)
        if invoice.line_items:
            for line in invoice.line_items:
                writer.writerow(
                    (*header, _text(line.description), _quantity(line.quantity), _amount(line.unit_price), _amount(line.line_total), *footer)
                )
        else:
            net = money.subtotal if money.subtotal is not None else money.total
            writer.writerow((*header, "", "1", _amount(net), _amount(net), *footer))
    return buffer.getvalue()


def _document(invoice: InvoiceAuditResponse) -> dict[str, Any]:
    money = invoice.financial_summary
    statuses = [anomaly.status.value for anomaly in invoice.anomalies]
    return {
        "external_id": str(invoice.invoice_id),
        "document_type": "VENDOR_BILL",
        "status": invoice.status.value,
        "supplier": {
            "name": invoice.vendor.name,
            "tax_id": invoice.vendor.tax_id,
            "external_id": str(invoice.vendor_on_file.id) if invoice.vendor_on_file else None,
        },
        "supplier_invoice_number": invoice.invoice_number,
        "document_date": _day(invoice.invoice_date) or None,
        "due_date": _day(invoice.due_date) or None,
        "currency": money.currency,
        "amounts": {
            "net": _amount(money.subtotal) or None,
            "tax": _amount(money.tax),
            "shipping": _amount(money.shipping),
            "discount": _amount(money.discount),
            "gross": _amount(money.total) or None,
        },
        "lines": [
            {
                "line_number": line.line_number,
                "description": line.description,
                "quantity": _quantity(line.quantity),
                "unit_price": _amount(line.unit_price),
                "amount": _amount(line.line_total),
            }
            for line in invoice.line_items
        ],
        "approval": (
            {
                "approved_at": invoice.approval.at.isoformat(),
                "approved_by": invoice.approval.by.name if invoice.approval.by else None,
                "method": invoice.approval.method,
            }
            if invoice.approval
            else None
        ),
        "attachment": {
            "filename": invoice.document.filename,
            "content_type": invoice.document.content_type,
            "sha256": invoice.document.sha256,
        },
        "audit": {
            "flags_raised": len(statuses),
            "flags_open": statuses.count("OPEN"),
            "flags_dismissed": statuses.count("DISMISSED"),
            "flags_confirmed": statuses.count("CONFIRMED"),
        },
    }


def _control_totals(invoices: Sequence[InvoiceAuditResponse]) -> list[dict[str, Any]]:
    """Per currency: how many documents and what they add up to, for the importer to reconcile."""
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "net": ZERO, "tax": ZERO, "gross": ZERO})
    for invoice in invoices:
        money = invoice.financial_summary
        bucket = totals[money.currency]
        bucket["count"] += 1
        bucket["net"] += money.subtotal or ZERO
        bucket["tax"] += money.tax
        bucket["gross"] += money.total or ZERO
    return [
        {
            "currency": currency,
            "document_count": bucket["count"],
            "net_amount": format_amount(bucket["net"]),
            "tax_amount": format_amount(bucket["tax"]),
            "gross_amount": format_amount(bucket["gross"]),
        }
        for currency, bucket in sorted(totals.items())
    ]


def erp_payload(
    invoices: Sequence[InvoiceAuditResponse],
    organization: Organization,
    *,
    scope: str,
    batch_id: UUID,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "format": ERP_FORMAT,
        "batch_id": str(batch_id),
        "generated_at": generated_at.isoformat(),
        "scope": scope,
        "source": {
            "system": "Billvery",
            "organization": {
                "id": str(organization.id),
                "name": organization.name,
                "legal_name": organization.legal_name,
                "country_code": organization.country_code,
                "currency_code": organization.currency_code,
            },
        },
        "document_count": len(invoices),
        "control_totals": _control_totals(invoices),
        "documents": [_document(invoice) for invoice in invoices],
    }
