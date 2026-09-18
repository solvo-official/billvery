"""The immutable audit trail (audit_logs) and tamper verification of stored originals."""

import asyncio
import hashlib
import secrets
import uuid

import asyncpg
import pytest
from alembic import command
from sqlalchemy import delete, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from invoice_auditor.cli import alembic_config
from invoice_auditor.models import AuditLog

from .conftest import create_database, drop_database
from .factories import file_metadata, invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration


async def trail(client, tenant, invoice_id):
    response = await client.get(f"/api/v1/invoices/{invoice_id}/audit-trail", headers=tenant.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def held(client, tenant, **extraction):
    body = (await process(client, tenant, invoice_payload(confidence=85, **extraction))).json()
    assert body["status"] == "NEEDS_REVIEW"
    return body


async def test_ingestion_records_the_file_hash_and_verdict(client, tenant):
    payload = invoice_payload()
    body = (await process(client, tenant, payload)).json()
    [entry] = await trail(client, tenant, body["invoice_id"])
    assert entry["action"] == "INGESTED"
    assert entry["file_sha256"] == payload["file"]["sha256"]
    assert entry["previous_status"] is None and entry["new_status"] == "APPROVED"
    assert entry["user_id"] is None  # submitted by an integration, not a person
    assert entry["details"] == {"source": "api", "findings": 0}


async def test_uploader_is_recorded_at_ingestion(client, tenant):
    payload = invoice_payload() | {"uploaded_by": str(tenant.reviewer_id)}
    body = (await process(client, tenant, payload)).json()
    [entry] = await trail(client, tenant, body["invoice_id"])
    assert entry["user_id"] == str(tenant.reviewer_id)
    assert entry["user_display_name"].startswith("reviewer-")
    assert entry["details"]["source"] == "upload"


async def test_resolution_is_signed_with_note_and_statuses(client, tenant):
    invoice = await held(client, tenant)
    anomaly_id = invoice["anomalies"][0]["id"]
    response = await client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"resolution": "DISMISSED", "resolved_by": str(tenant.reviewer_id), "note": "Totals re-added from the PDF"},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    ingested, resolved = await trail(client, tenant, invoice["invoice_id"])
    assert ingested["action"] == "INGESTED"
    assert resolved["action"] == "ANOMALY_DISMISSED"
    assert resolved["anomaly_id"] == anomaly_id
    assert resolved["user_id"] == str(tenant.reviewer_id)
    assert resolved["resolution_note"] == "Totals re-added from the PDF"
    assert (resolved["previous_status"], resolved["new_status"]) == ("NEEDS_REVIEW", "APPROVED")
    assert resolved["file_sha256"] == invoice["document"]["sha256"]
    assert resolved["details"] == {"anomaly_type": "LOW_EXTRACTION_CONFIDENCE", "severity": "HIGH"}


async def test_confirmation_records_the_rejection(client, tenant):
    invoice = await held(client, tenant)
    await client.post(
        f"/api/v1/anomalies/{invoice['anomalies'][0]['id']}/resolve",
        json={"resolution": "CONFIRMED", "resolved_by": str(tenant.reviewer_id), "note": "Vendor never issued it"},
        headers=tenant.headers,
    )
    entry = (await trail(client, tenant, invoice["invoice_id"]))[-1]
    assert (entry["action"], entry["new_status"]) == ("ANOMALY_CONFIRMED", "REJECTED")


async def test_approval_is_one_signed_entry_listing_the_dismissed_findings(client, tenant):
    invoice = await held(client, tenant, total_amount=1500)  # low confidence + math mismatch
    response = await client.post(
        f"/api/v1/invoices/{invoice['invoice_id']}/approve",
        json={"approved_by": str(tenant.reviewer_id), "note": "Confirmed with the vendor"},
        headers=tenant.headers,
    )
    assert response.status_code == 200, response.text
    entries = await trail(client, tenant, invoice["invoice_id"])
    assert [entry["action"] for entry in entries] == ["INGESTED", "APPROVED"]
    approved = entries[1]
    assert approved["resolution_note"] == "Confirmed with the vendor"
    assert (approved["previous_status"], approved["new_status"]) == ("NEEDS_REVIEW", "APPROVED")
    assert sorted(approved["details"]["dismissed_anomalies"]) == sorted(a["id"] for a in invoice["anomalies"])


async def test_entries_are_immutable_and_append_only(client, tenant, db):
    body = (await process(client, tenant, invoice_payload())).json()
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="immutable"):
            await session.execute(update(AuditLog).where(AuditLog.invoice_id == uuid.UUID(body["invoice_id"])).values(resolution_note="x"))
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(delete(AuditLog).where(AuditLog.invoice_id == uuid.UUID(body["invoice_id"])))


async def test_database_refuses_an_unsigned_resolution(client, tenant, db):
    invoice = await held(client, tenant)
    async with db.session() as session:
        await session.execute(
            text("UPDATE anomaly_logs SET status = 'DISMISSED', resolved_by = :by, resolved_at = now() WHERE invoice_id = :id"),
            {"by": tenant.reviewer_id, "id": invoice["invoice_id"]},
        )
        with pytest.raises(IntegrityError, match="without an audit_logs entry"):
            await session.commit()


async def test_review_entries_must_name_their_signer(client, tenant, db):
    invoice = await held(client, tenant)
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="review_signed"):
            await session.execute(
                text(
                    "INSERT INTO audit_logs (organization_id, invoice_id, action, new_status, file_sha256)"
                    " SELECT organization_id, id, 'APPROVED', 'APPROVED', file_hash FROM invoices WHERE id = :id"
                ),
                {"id": invoice["invoice_id"]},
            )


def test_rollout_backfills_what_the_previous_release_wrote_between_0005_and_0006():
    """0005 goes in while the previous release still serves: its unsigned reviews must keep
    working, and 0006 (after the deploy) signs them in retrospect before enforcing."""
    name, url = create_database()
    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)

    async def previous_release_writes() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        connection = await asyncpg.connect(dsn)
        try:
            organization = await connection.fetchval("INSERT INTO organizations (name) VALUES ('Rollout') RETURNING id")
            reviewer = await connection.fetchval(
                "INSERT INTO users (organization_id, email, role) VALUES ($1, 'reviewer@example.com', 'reviewer') RETURNING id", organization
            )
            invoice = await connection.fetchval(
                "INSERT INTO invoices (organization_id, currency, status, source_file_name, file_mime_type, file_size_bytes, file_hash)"
                " VALUES ($1, 'USD', 'NEEDS_REVIEW', 'a.pdf', 'application/pdf', 10, $2) RETURNING id",
                organization,
                "a" * 64,
            )
            finding = (
                "INSERT INTO anomaly_logs (invoice_id, organization_id, anomaly_type, severity, confidence_score, description)"
                " VALUES ($1, $2, 'LOW_EXTRACTION_CONFIDENCE', 'HIGH', 90, 'x') RETURNING id"
            )
            resolved = await connection.fetchval(finding, invoice, organization)
            still_open = await connection.fetchval(finding, invoice, organization)
            # The previous release resolves without writing audit_logs; at 0005 that must commit.
            await connection.execute(
                "UPDATE anomaly_logs SET status = 'DISMISSED', resolved_by = $1, resolved_at = now() WHERE id = $2", reviewer, resolved
            )
            return invoice, resolved, still_open
        finally:
            await connection.close()

    async def after_0006(invoice: uuid.UUID, resolved: uuid.UUID, still_open: uuid.UUID) -> None:
        connection = await asyncpg.connect(dsn)
        try:
            entries = await connection.fetch(
                "SELECT action, anomaly_id, details->>'backfilled' AS backfilled FROM audit_logs WHERE invoice_id = $1 ORDER BY action",
                invoice,
            )
            assert [(e["action"], e["anomaly_id"], e["backfilled"]) for e in entries] == [
                ("ANOMALY_DISMISSED", resolved, "true"),
                ("INGESTED", None, "true"),
            ]
            reviewer = await connection.fetchval("SELECT id FROM users LIMIT 1")
            with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError, match="without an audit_logs entry"):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE anomaly_logs SET status = 'DISMISSED', resolved_by = $1, resolved_at = now() WHERE id = $2",
                        reviewer,
                        still_open,
                    )
        finally:
            await connection.close()

    try:
        config = alembic_config(url)
        command.upgrade(config, "0005")
        ids = asyncio.run(previous_release_writes())
        command.upgrade(config, "head")
        asyncio.run(after_0006(*ids))
    finally:
        drop_database(name)


async def test_trail_is_private_to_the_tenant(client, tenant, db):
    body = (await process(client, tenant, invoice_payload())).json()
    other = await make_tenant(db, name="Other Firm")
    for path in ("audit-trail", "integrity"):
        response = await client.get(f"/api/v1/invoices/{body['invoice_id']}/{path}", headers=other.headers)
        assert response.status_code == 404


# --- Tamper verification ------------------------------------------------------------------------


async def stored_invoice(client, tenant, app):
    """An invoice whose original this service stored, as /upload does."""
    content = b"%PDF-1.7\n" + secrets.token_bytes(256)
    sha256 = hashlib.sha256(content).hexdigest()
    url = await app.state.documents.save(app.state.db, tenant.organization_id, sha256, content)
    payload = invoice_payload(content=content)
    payload["file"] = file_metadata(content) | {"storage_url": url}
    body = (await process(client, tenant, payload)).json()
    return body, app.state.documents.locate(url, tenant.organization_id)


async def integrity(client, tenant, invoice_id):
    response = await client.get(f"/api/v1/invoices/{invoice_id}/integrity", headers=tenant.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_untouched_original_verifies(client, tenant, app):
    body, _ = await stored_invoice(client, tenant, app)
    report = await integrity(client, tenant, body["invoice_id"])
    assert report["status"] == "verified"
    assert report["computed_sha256"] == report["recorded_sha256"] == report["ingested_sha256"] == body["document"]["sha256"]
    assert report["trail_consistent"] is True


async def test_modified_original_is_reported_as_tampered(client, tenant, app):
    body, path = await stored_invoice(client, tenant, app)
    path.write_bytes(path.read_bytes() + b"% appended after approval")
    report = await integrity(client, tenant, body["invoice_id"])
    assert report["status"] == "tampered"
    assert report["computed_sha256"] != report["recorded_sha256"]


async def test_rewritten_ledger_hash_is_caught_by_the_ingestion_record(client, tenant, app, db):
    body, _ = await stored_invoice(client, tenant, app)
    async with db.session() as session:
        await session.execute(text("UPDATE invoices SET file_hash = :h WHERE id = :id"), {"h": "0" * 64, "id": body["invoice_id"]})
        await session.commit()
    report = await integrity(client, tenant, body["invoice_id"])
    assert report["status"] == "tampered"
    assert report["ingested_sha256"] == body["document"]["sha256"]
    assert report["trail_consistent"] is False


async def test_api_submissions_without_an_original_are_unavailable(client, tenant):
    body = (await process(client, tenant, invoice_payload())).json()
    report = await integrity(client, tenant, body["invoice_id"])
    assert report["status"] == "unavailable"
    assert report["computed_sha256"] is None


async def test_stored_documents_cannot_be_rewritten(tenant, db):
    async with db.session() as session:
        await session.execute(
            text("INSERT INTO documents (organization_id, sha256, size_bytes, content) VALUES (:org, :sha, 1, '\\x00')"),
            {"org": tenant.organization_id, "sha": "a" * 64},
        )
        await session.commit()
        with pytest.raises(IntegrityError, match="immutable"):
            await session.execute(text("UPDATE documents SET content = '\\x01' WHERE sha256 = :sha"), {"sha": "a" * 64})
