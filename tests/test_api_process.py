import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update

from invoice_auditor.models import Invoice, TaxRateLimit, Vendor

from .factories import anomaly_types, invoice_payload, make_tenant, process, single_line

pytestmark = pytest.mark.integration


async def test_clean_invoice_is_approved(client, tenant, db):
    response = await process(client, tenant, invoice_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["anomalies"] == []
    assert body["financial_summary"] == {
        "currency": "USD",
        "subtotal": "1000.00",
        "tax": "50.00",
        "shipping": "0.00",
        "discount": "0.00",
        "total": "1050.00",
    }
    assert body["vendor"]["name"] == "ABC Trading LLC"
    assert body["vendor"]["confidence"] == 97
    assert body["ai_confidence"] == 95.0
    assert response.headers["x-request-id"]

    async with db.session() as session:
        invoice = await session.get(Invoice, uuid.UUID(body["invoice_id"]))
        vendor = await session.get(Vendor, invoice.vendor_id)
    assert (vendor.normalized_name, vendor.tax_id) == ("abc trading", "100200300400003")
    assert invoice.metadata_ == {"department": "Operations"}
    assert invoice.invoice_number_normalized == "INV1001"


async def test_get_audit_matches_process_response(client, tenant):
    created = (await process(client, tenant, invoice_payload(confidence=80))).json()
    response = await client.get(f"/api/v1/invoices/{created['invoice_id']}/audit", headers=tenant.headers)
    assert response.status_code == 200
    assert response.json() == created


async def test_duplicate_file_is_critical_and_scoped_to_tenant(client, tenant, db):
    content = secrets.token_bytes(128)
    first = await process(client, tenant, invoice_payload(content=content))
    second = (await process(client, tenant, invoice_payload(content=content))).json()

    assert second["status"] == "NEEDS_REVIEW"
    # The identical file is reported once, not again as a duplicate invoice number.
    assert anomaly_types(second) == {"DUPLICATE_FILE_HASH": "CRITICAL"}
    [anomaly] = second["anomalies"]
    assert anomaly["detected_value"]["duplicate_of"][0]["invoice_id"] == first.json()["invoice_id"]

    other_tenant = await make_tenant(db, name="Other Firm")
    third = (await process(client, other_tenant, invoice_payload(content=content))).json()
    assert third["status"] == "APPROVED"
    assert third["anomalies"] == []


async def test_duplicate_invoice_number_across_vendor_spellings(client, tenant):
    await process(client, tenant, invoice_payload(vendor_name="ABC Trading LLC", invoice_number="INV-1001"))
    body = (
        await process(client, tenant, invoice_payload(vendor_name="ABC Trading L.L.C.", invoice_number="inv 1001"))
    ).json()
    assert anomaly_types(body) == {"DUPLICATE_INVOICE_NUMBER": "HIGH"}
    assert body["status"] == "NEEDS_REVIEW"


async def test_duplicate_window_is_90_days(client, tenant, db):
    first = (await process(client, tenant, invoice_payload())).json()
    async with db.session() as session:
        await session.execute(
            update(Invoice)
            .where(Invoice.id == uuid.UUID(first["invoice_id"]))
            .values(created_at=datetime.now(UTC) - timedelta(days=91))
        )
        await session.commit()
    body = (await process(client, tenant, invoice_payload())).json()
    assert "DUPLICATE_INVOICE_NUMBER" not in anomaly_types(body)


async def test_similar_invoice_number_same_date_and_total(client, tenant):
    await process(client, tenant, invoice_payload(invoice_number="INV-10022"))
    body = (await process(client, tenant, invoice_payload(invoice_number="INV-1O022"))).json()  # OCR: O for 0
    assert anomaly_types(body) == {"SIMILAR_INVOICE_NUMBER": "MEDIUM"}
    assert body["status"] == "APPROVED"  # MEDIUM findings do not block

    sequential = (await process(client, tenant, invoice_payload(invoice_number="INV-10023", total_amount=1050))).json()
    assert "SIMILAR_INVOICE_NUMBER" in anomaly_types(sequential)  # same date and total: still suspicious
    next_month = (
        await process(
            client,
            tenant,
            invoice_payload(invoice_number="INV-10024", invoice_date=datetime.now(UTC).date().isoformat()),
        )
    ).json()
    assert "SIMILAR_INVOICE_NUMBER" not in anomaly_types(next_month)


async def test_look_alike_vendor_is_reported_and_searched_for_duplicates(client, tenant, db):
    await process(client, tenant, invoice_payload(vendor_tax_id=None))
    body = (await process(client, tenant, invoice_payload(vendor_name="ABC Tradings", vendor_tax_id=None))).json()
    types = anomaly_types(body)
    assert types["SIMILAR_VENDOR_NAME"] == "MEDIUM"
    assert types["DUPLICATE_INVOICE_NUMBER"] == "HIGH"  # same number under the look-alike vendor
    similar = next(a for a in body["anomalies"] if a["type"] == "SIMILAR_VENDOR_NAME")
    assert similar["expected_value"]["similar_vendors"][0]["name"] == "ABC Trading LLC"

    async with db.session() as session:
        assert await session.scalar(select(func.count()).select_from(Vendor)) == 2  # never auto-merged


async def test_vendor_tax_id_mismatch(client, tenant):
    await process(client, tenant, invoice_payload(invoice_number="A-1"))
    body = (
        await process(client, tenant, invoice_payload(invoice_number="A-2", vendor_tax_id="999-888-777"))
    ).json()
    assert anomaly_types(body) == {"VENDOR_TAX_ID_MISMATCH": "HIGH"}


async def test_vendor_name_mismatch_for_known_tax_id(client, tenant):
    await process(client, tenant, invoice_payload(invoice_number="A-1"))
    body = (
        await process(client, tenant, invoice_payload(invoice_number="A-2", vendor_name="Totally Different Co"))
    ).json()
    assert anomaly_types(body) == {"VENDOR_NAME_MISMATCH": "MEDIUM"}
    assert body["vendor"]["name"] == "Totally Different Co"  # as printed; vendor id is the tax ID's owner


async def test_vendor_tax_id_is_learned(client, tenant, db):
    await process(client, tenant, invoice_payload(vendor_tax_id=None, invoice_number="A-1"))
    await process(client, tenant, invoice_payload(vendor_tax_id="TRN 555", invoice_number="A-2"))
    async with db.session() as session:
        vendor = await session.scalar(select(Vendor))
    assert vendor.tax_id == "TRN555"


@pytest.mark.parametrize(
    ("total", "severity", "status"),
    [(1100, "MEDIUM", "APPROVED"), (1500, "HIGH", "NEEDS_REVIEW")],
)
async def test_math_mismatch(client, tenant, total, severity, status):
    body = (await process(client, tenant, invoice_payload(total_amount=total))).json()
    assert anomaly_types(body) == {"MATH_TOTAL_MISMATCH": severity}
    assert body["status"] == status


async def test_math_uses_shipping_and_discount(client, tenant):
    body = (
        await process(client, tenant, invoice_payload(shipping_amount=30, discount_amount=-80, total_amount=1000))
    ).json()
    assert body["anomalies"] == []
    assert body["financial_summary"]["discount"] == "80.00"


async def test_price_spike_against_organization_history(client, tenant):
    for number in range(3):
        await process(client, tenant, invoice_payload(invoice_number=f"P-{number}", **single_line("Printer cartridge", 50)))

    below = (await process(client, tenant, invoice_payload(invoice_number="P-3", **single_line("Printer Cartridge", 120)))).json()
    assert "PRICE_SPIKE" not in anomaly_types(below)

    spike = (
        await process(client, tenant, invoice_payload(invoice_number="P-4", **single_line("printer cartridge!", 400)))
    ).json()
    assert anomaly_types(spike) == {"PRICE_SPIKE": "HIGH"}
    [anomaly] = spike["anomalies"]
    assert anomaly["expected_value"]["samples"] == 4
    assert anomaly["expected_value"]["historical_average_unit_price"] == "67.50"  # (50 * 3 + 120) / 4


async def test_price_spike_needs_minimum_history(client, tenant):
    for number in range(2):
        await process(client, tenant, invoice_payload(invoice_number=f"P-{number}", **single_line("Toner", 50)))
    body = (await process(client, tenant, invoice_payload(invoice_number="P-9", **single_line("Toner", 500)))).json()
    assert "PRICE_SPIKE" not in anomaly_types(body)


async def test_tax_rate_limit_by_country_and_override(client, db):
    async with db.session() as session:
        session.add(TaxRateLimit(country_code="AE", max_rate_percent=5, note="UAE VAT"))
        await session.commit()
    uae = await make_tenant(db, country_code="AE", currency_code="AED")
    body = (await process(client, uae, invoice_payload(currency="AED", tax_amount=100, total_amount=1100))).json()
    assert anomaly_types(body) == {"TAX_RATE_EXCEEDED": "HIGH"}
    assert body["anomalies"][0]["expected_value"]["limit_source"] == "AE country limit"

    exempt = await make_tenant(db, country_code="AE", settings={"max_tax_rate_percent": "12.5"})
    body = (await process(client, exempt, invoice_payload(tax_amount=100, total_amount=1100))).json()
    assert body["anomalies"] == []


@pytest.mark.parametrize(("confidence", "severity"), [(85, "HIGH"), (60, "CRITICAL")])
async def test_low_extraction_confidence_needs_review(client, tenant, confidence, severity):
    body = (await process(client, tenant, invoice_payload(confidence=confidence))).json()
    assert anomaly_types(body) == {"LOW_EXTRACTION_CONFIDENCE": severity}
    assert body["status"] == "NEEDS_REVIEW"


async def test_future_date_and_missing_fields(client, tenant):
    future = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
    body = (
        await process(client, tenant, invoice_payload(invoice_date=future, invoice_number="", vendor_name=None))
    ).json()
    types = anomaly_types(body)
    assert types["FUTURE_INVOICE_DATE"] == "HIGH"
    assert types["MISSING_REQUIRED_FIELDS"] == "MEDIUM"
    missing = next(a for a in body["anomalies"] if a["type"] == "MISSING_REQUIRED_FIELDS")
    assert missing["detected_value"]["missing_fields"] == ["vendor_name", "invoice_number"]
    assert body["anomalies"][0]["severity"] == "HIGH"  # most severe first


async def test_currency_defaults_to_organization(client, db):
    tenant = await make_tenant(db, currency_code="KWD")
    body = (await process(client, tenant, invoice_payload(currency=None, total_amount=1050.125, tax_amount=50.125))).json()
    assert body["financial_summary"]["currency"] == "KWD"
    assert body["financial_summary"]["total"] == "1050.125"  # three-decimal currency kept exactly


async def test_line_totals_and_unit_prices_are_derived(client, tenant, db):
    payload = invoice_payload(
        line_items=[
            {"description": "Hours", "quantity": 7.5, "unit_price": 100},
            {"description": "Licence", "quantity": 3, "line_total": 250},
        ],
        subtotal=1000,
        total_amount=1050,
    )
    body = (await process(client, tenant, payload)).json()
    async with db.session() as session:
        invoice = await session.get(Invoice, uuid.UUID(body["invoice_id"]))
        rows = (
            await session.execute(
                text("SELECT line_total, unit_price FROM invoice_items WHERE invoice_id = :id ORDER BY line_number"),
                {"id": invoice.id},
            )
        ).all()
    assert [(str(total), str(price)) for total, price in rows] == [("750.0000", "100.0000"), ("250.0000", "83.3333")]


async def test_idempotency_key_replays_and_detects_reuse(client, tenant, db):
    payload = invoice_payload()
    first = await process(client, tenant, payload, idempotency_key="order-42")
    replay = await process(client, tenant, payload, idempotency_key="order-42")
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json() == first.json()

    reused = await process(client, tenant, invoice_payload(), idempotency_key="order-42")
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "conflict"
    async with db.session() as session:
        assert await session.scalar(select(func.count()).select_from(Invoice)) == 1


async def test_concurrent_submissions_of_one_file_are_serialized(client, tenant):
    content = secrets.token_bytes(64)
    responses = await asyncio.gather(*(process(client, tenant, invoice_payload(content=content)) for _ in range(4)))
    assert all(r.status_code == 201 for r in responses)
    flagged = [r.json() for r in responses if "DUPLICATE_FILE_HASH" in anomaly_types(r.json())]
    assert len(flagged) == 3  # exactly one submission got through clean


async def test_uploaded_by_must_belong_to_the_organization(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    ok = await process(client, tenant, invoice_payload() | {"uploaded_by": str(tenant.member_id)})
    assert ok.status_code == 201
    response = await process(client, tenant, invoice_payload() | {"uploaded_by": str(other.member_id)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"


async def test_validation_errors_use_the_error_envelope(client, tenant):
    payload = invoice_payload()
    payload["file"]["sha256"] = "not-a-hash"
    payload["unexpected"] = True
    response = await process(client, tenant, payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {tuple(d["loc"]) for d in error["details"]} >= {("body", "file", "sha256"), ("body", "unexpected")}


async def test_audit_of_unknown_or_foreign_invoice_is_404(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    foreign = (await process(client, other, invoice_payload())).json()
    for invoice_id in (foreign["invoice_id"], str(uuid.uuid4())):
        response = await client.get(f"/api/v1/invoices/{invoice_id}/audit", headers=tenant.headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
