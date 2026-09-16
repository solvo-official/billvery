"""Endpoints the reviewer console uses: feed, upload stream, originals, organization, health."""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from invoice_auditor.ingestion.gemini import ExtractionError
from invoice_auditor.schemas.extraction import InvoiceExtraction

from .factories import BASE_EXTRACTION, invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


class FakeExtractor:
    """Stands in for Gemini: returns a fixed extraction (or raises) and counts calls."""

    model = "fake-gemini"

    def __init__(self, result: InvoiceExtraction | Exception) -> None:
        self.result = result
        self.calls = 0

    async def extract(self, document: bytes, mime_type: str) -> InvoiceExtraction:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def extraction(**overrides) -> InvoiceExtraction:
    today = datetime.now().date()
    fields = {**BASE_EXTRACTION, "invoice_date": (today - timedelta(days=4)).isoformat(), **overrides}
    return InvoiceExtraction.model_validate(fields)


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


async def upload(client, tenant, content: bytes = PDF, filename: str = "pioneer-inv-88214.pdf", **form) -> list[dict]:
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=tenant.headers,
        files={"file": (filename, content, "application/pdf")},
        data={key: str(value) for key, value in form.items()},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return events(response)


# --- Feed ---------------------------------------------------------------------------------------


async def test_list_returns_newest_first_with_line_items_and_anomalies(client, tenant):
    first = (await process(client, tenant, invoice_payload(invoice_number="A-1"))).json()
    second = (await process(client, tenant, invoice_payload(invoice_number="A-2", total_amount=1500))).json()

    page = (await client.get("/api/v1/invoices", headers=tenant.headers)).json()
    assert [item["invoice_id"] for item in page["items"]] == [second["invoice_id"], first["invoice_id"]]
    assert page["next_cursor"] is None
    newest = page["items"][0]
    assert newest["anomalies"][0]["type"] == "MATH_TOTAL_MISMATCH"
    assert [line["line_total"] for line in newest["line_items"]] == ["500.00", "500.00"]
    assert newest["vendor_on_file"] == {
        "id": newest["vendor"]["id"],
        "name": "ABC Trading LLC",
        "tax_id": "100200300400003",
        "first_seen": False,
    }
    assert page["items"][1]["vendor_on_file"]["first_seen"] is True
    assert newest["source"] == "api" and newest["submitted_by"] is None
    assert newest["document"]["available"] is False  # /process only records where the file lives
    assert datetime.fromisoformat(page["server_time"])


async def test_list_filters_search_and_pages(client, tenant):
    for number in range(5):
        await process(client, tenant, invoice_payload(invoice_number=f"P-{number}", confidence=85 if number % 2 else 95))

    review = (await client.get("/api/v1/invoices", params={"status": "NEEDS_REVIEW"}, headers=tenant.headers)).json()
    assert {item["invoice_number"] for item in review["items"]} == {"P-1", "P-3"}

    found = (await client.get("/api/v1/invoices", params={"q": "p-4"}, headers=tenant.headers)).json()
    assert [item["invoice_number"] for item in found["items"]] == ["P-4"]

    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/v1/invoices", params=params, headers=tenant.headers)).json()
        seen += [item["invoice_number"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ["P-4", "P-3", "P-2", "P-1", "P-0"]

    bad = await client.get("/api/v1/invoices", params={"cursor": "not-a-cursor"}, headers=tenant.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_parameter"


async def test_search_treats_wildcards_literally(client, tenant):
    await process(client, tenant, invoice_payload(invoice_number="100%-OFF"))
    await process(client, tenant, invoice_payload(invoice_number="100X-OFF"))
    found = (await client.get("/api/v1/invoices", params={"q": "100%"}, headers=tenant.headers)).json()
    assert [item["invoice_number"] for item in found["items"]] == ["100%-OFF"]


async def test_polling_with_updated_since_sees_new_invoices_and_resolutions(client, tenant):
    flagged = (await process(client, tenant, invoice_payload(confidence=85))).json()
    baseline = (await client.get("/api/v1/invoices", headers=tenant.headers)).json()["server_time"]

    quiet = (await client.get("/api/v1/invoices", params={"updated_since": baseline}, headers=tenant.headers)).json()
    assert quiet["items"] == []

    anomaly_id = flagged["anomalies"][0]["id"]
    await client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"resolution": "DISMISSED", "resolved_by": str(tenant.reviewer_id), "note": "checked"},
        headers=tenant.headers,
    )
    fresh = (await process(client, tenant, invoice_payload(invoice_number="NEW-1"))).json()
    changed = (await client.get("/api/v1/invoices", params={"updated_since": baseline}, headers=tenant.headers)).json()
    by_id = {item["invoice_id"]: item for item in changed["items"]}
    assert set(by_id) == {flagged["invoice_id"], fresh["invoice_id"]}
    assert by_id[flagged["invoice_id"]]["status"] == "APPROVED"


async def test_list_is_scoped_to_the_key_s_organization(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    await process(client, other, invoice_payload())
    page = (await client.get("/api/v1/invoices", headers=tenant.headers)).json()
    assert page["items"] == []


# --- Upload -------------------------------------------------------------------------------------


async def test_upload_streams_phases_and_stores_the_original(app, client, tenant):
    extractor = FakeExtractor(extraction(total_amount=1500))
    app.state.extractor = extractor
    stream = await upload(client, tenant, uploaded_by=tenant.reviewer_id, metadata=json.dumps({"department": "Fleet"}))

    assert [e["event"] for e in stream] == ["received", "extracting", "auditing", "complete"]
    assert stream[0]["size_bytes"] == len(PDF) and stream[0]["content_type"] == "application/pdf"
    assert stream[1]["model"] == "fake-gemini" and stream[1]["reused"] is False
    audit = stream[-1]["audit"]
    assert audit["status"] == "NEEDS_REVIEW"
    assert audit["anomalies"][0]["type"] == "MATH_TOTAL_MISMATCH"
    assert audit["anomalies"][0]["expected_value"]["total_amount"] == "1050.00"
    assert audit["source"] == "upload"
    assert audit["submitted_by"]["id"] == str(tenant.reviewer_id)
    assert audit["document"]["available"] is True
    assert audit["document"]["filename"] == "pioneer-inv-88214.pdf"

    original = await client.get(f"/api/v1/invoices/{audit['invoice_id']}/document", headers=tenant.headers)
    assert original.status_code == 200
    assert original.content == PDF
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["content-disposition"].startswith("inline")


async def test_reupload_reuses_extraction_and_is_flagged_duplicate(app, client, tenant):
    extractor = FakeExtractor(extraction())
    app.state.extractor = extractor
    first = (await upload(client, tenant))[-1]["audit"]
    second_stream = await upload(client, tenant, filename="same-file-again.pdf")

    assert extractor.calls == 1
    assert second_stream[1]["reused"] is True
    second = second_stream[-1]["audit"]
    assert {a["type"] for a in second["anomalies"]} == {"DUPLICATE_FILE_HASH"}
    assert second["anomalies"][0]["detected_value"]["duplicate_of"][0]["invoice_id"] == first["invoice_id"]


async def test_upload_replays_an_idempotency_key(app, client, tenant):
    app.state.extractor = FakeExtractor(extraction())
    headers = tenant.headers | {"Idempotency-Key": "batch-7"}
    files = {"file": ("inv.pdf", PDF, "application/pdf")}
    first = events(await client.post("/api/v1/invoices/upload", headers=headers, files=files))[-1]
    again = events(await client.post("/api/v1/invoices/upload", headers=headers, files=files))
    assert [e["event"] for e in again] == ["received", "complete"]
    assert again[-1]["replayed"] is True and again[-1]["audit"]["invoice_id"] == first["audit"]["invoice_id"]


async def test_extraction_failure_is_reported_in_the_stream(app, client, tenant):
    app.state.extractor = FakeExtractor(ExtractionError("Gemini rejected the request (404 NOT_FOUND): model not found"))
    stream = await upload(client, tenant)
    assert stream[-1] == {
        "event": "error",
        "error": {"code": "extraction_failed", "message": "Gemini rejected the request (404 NOT_FOUND): model not found"},
    }
    page = (await client.get("/api/v1/invoices", headers=tenant.headers)).json()
    assert page["items"] == []  # nothing half-saved


@pytest.mark.parametrize(
    ("content", "filename", "status", "code"),
    [
        (b"GIF89a....", "photo.gif", 415, "unsupported_media_type"),
        (b"", "empty.pdf", 422, "invalid_parameter"),
    ],
)
async def test_upload_rejects_bad_files_before_streaming(app, client, tenant, content, filename, status, code):
    app.state.extractor = FakeExtractor(extraction())
    response = await client.post("/api/v1/invoices/upload", headers=tenant.headers, files={"file": (filename, content, "application/pdf")})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert app.state.extractor.calls == 0


async def test_upload_rejects_oversized_files(app, client, tenant, settings):
    app.state.extractor = FakeExtractor(extraction())
    too_big = PDF + b"0" * (settings.max_upload_bytes + 1)
    response = await client.post("/api/v1/invoices/upload", headers=tenant.headers, files={"file": ("big.pdf", too_big, "application/pdf")})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_upload_needs_gemini_configured(client, tenant):
    response = await client.post("/api/v1/invoices/upload", headers=tenant.headers, files={"file": ("inv.pdf", PDF, "application/pdf")})
    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["error"]["message"]


async def test_upload_checks_uploaded_by_before_extracting(app, client, tenant, db):
    app.state.extractor = FakeExtractor(extraction())
    other = await make_tenant(db, name="Other Firm")
    response = await client.post(
        "/api/v1/invoices/upload",
        headers=tenant.headers,
        files={"file": ("inv.pdf", PDF, "application/pdf")},
        data={"uploaded_by": str(other.reviewer_id)},
    )
    assert response.status_code == 422
    assert app.state.extractor.calls == 0


async def test_document_of_a_processed_invoice_or_another_tenant_is_404(app, client, tenant, db):
    processed = (await process(client, tenant, invoice_payload())).json()
    missing = await client.get(f"/api/v1/invoices/{processed['invoice_id']}/document", headers=tenant.headers)
    assert missing.status_code == 404

    app.state.extractor = FakeExtractor(extraction())
    uploaded = (await upload(client, tenant))[-1]["audit"]
    other = await make_tenant(db, name="Other Firm")
    foreign = await client.get(f"/api/v1/invoices/{uploaded['invoice_id']}/document", headers=other.headers)
    assert foreign.status_code == 404


# --- Organization, users, health ---------------------------------------------------------------------


async def test_organization_and_users(client, tenant):
    organization = (await client.get("/api/v1/organization", headers=tenant.headers)).json()
    assert organization["id"] == str(tenant.organization_id)
    assert organization["currency_code"] == "USD"

    users = (await client.get("/api/v1/users", headers=tenant.headers)).json()
    by_id = {user["id"]: user for user in users}
    assert by_id[str(tenant.reviewer_id)]["can_resolve"] is True
    assert by_id[str(tenant.member_id)]["can_resolve"] is False


async def test_health_reports_extraction(app, client):
    health = (await client.get("/healthz")).json()
    assert health == {
        "status": "ok",
        "version": "1.0.0",
        "database": "ok",
        "extraction": {"configured": False, "model": "gemini-3.1-pro-preview"},
        "auth": {"google": False, "session": True},
        "limits": {"max_upload_bytes": 18 * 1024 * 1024},
    }
    app.state.extractor = FakeExtractor(extraction())
    assert (await client.get("/healthz")).json()["extraction"]["configured"] is True


async def test_audit_response_includes_line_items_and_document(client, tenant):
    created = (await process(client, tenant, invoice_payload())).json()
    audit = (await client.get(f"/api/v1/invoices/{created['invoice_id']}/audit", headers=tenant.headers)).json()
    assert audit["line_items"][0] == {
        "line_number": 1,
        "description": "Laptop stand",
        "quantity": "2.00",
        "unit_price": "250.00",
        "line_total": "500.00",
    }
    assert audit["document"]["sha256"] == created["document"]["sha256"]
    assert uuid.UUID(audit["vendor_on_file"]["id"])
