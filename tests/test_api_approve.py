"""Approve & archive, and the approval stamp on every path to APPROVED."""

import uuid

import pytest
from sqlalchemy import text

from .factories import invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration


async def approve(client, tenant, invoice_id, approved_by=None, note="Checked against the PO"):
    body = {"approved_by": str(approved_by or tenant.reviewer_id)}
    if note is not None:
        body["note"] = note
    return await client.post(f"/api/v1/invoices/{invoice_id}/approve", json=body, headers=tenant.headers)


async def held(client, tenant, **extraction):
    body = (await process(client, tenant, invoice_payload(**extraction))).json()
    assert body["status"] == "NEEDS_REVIEW"
    return body


async def test_approve_dismisses_open_findings_and_stamps_the_reviewer(client, tenant):
    invoice = await held(client, tenant, confidence=85, total_amount=1500, invoice_number="")
    assert len(invoice["anomalies"]) == 3  # low confidence, math, missing number

    response = await approve(client, tenant, invoice["invoice_id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["approval"]["method"] == "reviewer"
    assert body["approval"]["by"]["id"] == str(tenant.reviewer_id)
    assert {a["status"] for a in body["anomalies"]} == {"DISMISSED"}
    assert {a["resolution_note"] for a in body["anomalies"]} == {"Checked against the PO"}
    assert {a["resolved_by"] for a in body["anomalies"]} == {str(tenant.reviewer_id)}


async def test_approve_without_a_note_records_the_default(client, tenant):
    invoice = await held(client, tenant, confidence=85)
    body = (await approve(client, tenant, invoice["invoice_id"], note=None)).json()
    assert body["anomalies"][0]["resolution_note"] == "Approved and archived."


async def test_approval_is_visible_to_pollers(client, tenant):
    invoice = await held(client, tenant, confidence=85)
    since = (await client.get("/api/v1/invoices", headers=tenant.headers)).json()["server_time"]
    await approve(client, tenant, invoice["invoice_id"])
    changed = (await client.get("/api/v1/invoices", params={"updated_since": since}, headers=tenant.headers)).json()
    assert [item["status"] for item in changed["items"]] == ["APPROVED"]


async def test_automatic_approval_is_stamped_without_a_reviewer(client, tenant):
    body = (await process(client, tenant, invoice_payload())).json()
    assert body["status"] == "APPROVED"
    assert body["approval"]["method"] == "automatic"
    assert body["approval"]["by"] is None
    assert body["approval"]["at"] == body["processed_at"]


async def test_approval_through_flag_decisions_records_the_reviewer(client, tenant):
    invoice = await held(client, tenant, confidence=85)
    await client.post(
        f"/api/v1/anomalies/{invoice['anomalies'][0]['id']}/resolve",
        json={"resolution": "DISMISSED", "resolved_by": str(tenant.reviewer_id), "note": "checked"},
        headers=tenant.headers,
    )
    audit = (await client.get(f"/api/v1/invoices/{invoice['invoice_id']}/audit", headers=tenant.headers)).json()
    assert audit["approval"]["method"] == "reviewer"
    assert audit["approval"]["by"]["id"] == str(tenant.reviewer_id)


async def test_only_invoices_in_review_can_be_approved(client, tenant):
    approved = (await process(client, tenant, invoice_payload(invoice_number="A-1"))).json()
    already = await approve(client, tenant, approved["invoice_id"])
    assert already.status_code == 409 and "already approved" in already.json()["error"]["message"]

    rejected = await held(client, tenant, invoice_number="R-1", total_amount=1500)
    await client.post(
        f"/api/v1/anomalies/{rejected['anomalies'][0]['id']}/resolve",
        json={"resolution": "CONFIRMED", "resolved_by": str(tenant.reviewer_id), "note": "real"},
        headers=tenant.headers,
    )
    refused = await approve(client, tenant, rejected["invoice_id"])
    assert refused.status_code == 409 and refused.json()["error"]["details"] == {"status": "REJECTED"}


async def test_approver_must_be_a_reviewer_in_the_organization(client, tenant, db):
    invoice = await held(client, tenant, confidence=85)
    member = await approve(client, tenant, invoice["invoice_id"], approved_by=tenant.member_id)
    assert member.status_code == 403

    other = await make_tenant(db, name="Other Firm")
    outsider = await approve(client, tenant, invoice["invoice_id"], approved_by=other.reviewer_id)
    assert outsider.status_code == 422

    foreign = await approve(client, other, invoice["invoice_id"], approved_by=other.reviewer_id)
    assert foreign.status_code == 404
    missing = await approve(client, tenant, uuid.uuid4())
    assert missing.status_code == 404


async def test_database_stamps_approvals_from_any_writer(client, tenant, db):
    invoice = await held(client, tenant, confidence=85)
    async with db.session() as session:
        await session.execute(
            text("UPDATE anomaly_logs SET status = 'DISMISSED', resolved_by = :by, resolved_at = now() WHERE invoice_id = :id"),
            {"by": tenant.reviewer_id, "id": invoice["invoice_id"]},
        )
        await session.execute(text("UPDATE invoices SET status = 'APPROVED' WHERE id = :id"), {"id": invoice["invoice_id"]})
        await session.commit()
        stamped = await session.scalar(text("SELECT approved_at FROM invoices WHERE id = :id"), {"id": invoice["invoice_id"]})
    assert stamped is not None
