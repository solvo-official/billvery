"""Batch exports for accounting systems: QuickBooks / Xero CSV and the SAP / NetSuite JSON batch."""

import csv
import io
import json

import pytest

from .factories import invoice_payload, process

EXPORT = "/api/v1/invoices/export"
CSV_HEADER = [
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
]

pytestmark = pytest.mark.integration


async def ledger(client, tenant):
    """Two approved bills and one held for review."""
    first = (await process(client, tenant, invoice_payload(invoice_number="A-1"))).json()
    second = (await process(client, tenant, invoice_payload(invoice_number="A-2", vendor_name="=HYPERLINK(evil)"))).json()
    held = (await process(client, tenant, invoice_payload(invoice_number="H-1", confidence=85))).json()
    return first, second, held


def rows(response):
    return list(csv.reader(io.StringIO(response.text)))


async def test_accounting_csv_has_one_row_per_line_item(client, tenant):
    await ledger(client, tenant)
    response = await client.post(EXPORT, json={"format": "accounting_csv", "scope": "approved"}, headers=tenant.headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-export-count"] == "2"
    table = rows(response)
    assert table[0] == CSV_HEADER
    assert len(table) == 1 + 2 * 2  # two approved invoices, two lines each
    first = dict(zip(CSV_HEADER, table[1], strict=True))
    assert first["InvoiceNumber"] == "A-1"
    assert (first["LineItemDescription"], first["LineItemQuantity"], first["LineItemUnitPrice"], first["LineItemAmount"]) == (
        "Laptop stand",
        "2",
        "250.00",
        "500.00",
    )
    assert (first["TaxAmount"], first["TotalAmount"], first["Currency"]) == ("50.00", "1050.00", "USD")
    assert {row[0] for row in table[1:]} == {"A-1", "A-2"}  # nothing that isn't approved


async def test_document_text_cannot_become_a_spreadsheet_formula(client, tenant):
    await ledger(client, tenant)
    table = rows(await client.post(EXPORT, json={"format": "accounting_csv", "scope": "approved"}, headers=tenant.headers))
    vendors = {row[1] for row in table[1:]}
    assert "'=HYPERLINK(evil)" in vendors


async def test_all_and_selected_scopes(client, tenant):
    first, _, held = await ledger(client, tenant)
    everything = await client.post(EXPORT, json={"format": "accounting_csv", "scope": "all"}, headers=tenant.headers)
    assert everything.headers["x-export-count"] == "3"
    chosen = await client.post(
        EXPORT,
        json={"format": "accounting_csv", "scope": "selected", "invoice_ids": [first["invoice_id"], held["invoice_id"]]},
        headers=tenant.headers,
    )
    assert {row[0] for row in rows(chosen)[1:]} == {"A-1", "H-1"}


async def test_erp_json_batch(client, tenant):
    first, second, _ = await ledger(client, tenant)
    response = await client.post(EXPORT, json={"format": "erp_json", "scope": "approved"}, headers=tenant.headers)
    assert response.status_code == 200, response.text
    batch = json.loads(response.text)
    assert batch["format"] == "billvery.ap-batch/v1"
    assert batch["batch_id"] == response.headers["x-export-batch-id"]
    assert batch["document_count"] == 2
    assert batch["control_totals"] == [
        {"currency": "USD", "document_count": 2, "net_amount": "2000.00", "tax_amount": "100.00", "gross_amount": "2100.00"}
    ]
    document = next(d for d in batch["documents"] if d["external_id"] == first["invoice_id"])
    assert document["supplier_invoice_number"] == "A-1"
    assert document["supplier"]["external_id"] == first["vendor_on_file"]["id"]
    assert document["amounts"] == {"net": "1000.00", "tax": "50.00", "shipping": "0.00", "discount": "0.00", "gross": "1050.00"}
    assert document["lines"][0] == {"line_number": 1, "description": "Laptop stand", "quantity": "2", "unit_price": "250.00", "amount": "500.00"}
    assert document["approval"]["method"] == "automatic"
    assert document["attachment"]["sha256"] == first["document"]["sha256"]


async def test_selection_must_exist_in_this_workspace(client, tenant):
    await ledger(client, tenant)
    response = await client.post(
        EXPORT,
        json={"format": "accounting_csv", "scope": "selected", "invoice_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=tenant.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"


async def test_scope_and_ids_must_agree(client, tenant):
    missing = await client.post(EXPORT, json={"format": "erp_json", "scope": "selected"}, headers=tenant.headers)
    assert missing.status_code == 422
    stray = await client.post(
        EXPORT, json={"format": "erp_json", "scope": "all", "invoice_ids": ["00000000-0000-0000-0000-000000000000"]}, headers=tenant.headers
    )
    assert stray.status_code == 422


async def test_batches_are_capped(client, tenant, app):
    await ledger(client, tenant)
    app.state.settings.export_max_invoices = 2
    response = await client.post(EXPORT, json={"format": "erp_json", "scope": "all"}, headers=tenant.headers)
    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"count": 3, "limit": 2}
