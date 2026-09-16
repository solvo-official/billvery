import uuid

import pytest

from .factories import invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration


async def resolve(client, tenant, anomaly_id, resolution="DISMISSED", resolved_by=None, note="checked"):
    return await client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"resolution": resolution, "resolved_by": str(resolved_by or tenant.reviewer_id), "note": note},
        headers=tenant.headers,
    )


async def flagged_invoice(client, tenant, **extraction):
    body = (await process(client, tenant, invoice_payload(**extraction))).json()
    assert body["status"] == "NEEDS_REVIEW"
    return body


async def test_dismissing_the_only_blocking_anomaly_approves(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85)
    [anomaly] = invoice["anomalies"]
    response = await resolve(client, tenant, anomaly["id"], note="Totals checked against the PDF")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invoice_status"] == "APPROVED"
    assert body["anomaly"]["status"] == "DISMISSED"
    assert body["anomaly"]["resolved_by"] == str(tenant.reviewer_id)
    assert body["anomaly"]["resolution_note"] == "Totals checked against the PDF"
    assert body["anomaly"]["resolved_at"]

    audit = (await client.get(f"/api/v1/invoices/{invoice['invoice_id']}/audit", headers=tenant.headers)).json()
    assert audit["status"] == "APPROVED"
    assert audit["anomalies"][0]["status"] == "DISMISSED"


async def test_confirming_a_blocking_anomaly_rejects(client, tenant):
    invoice = await flagged_invoice(client, tenant, total_amount=1500)
    body = (await resolve(client, tenant, invoice["anomalies"][0]["id"], "CONFIRMED")).json()
    assert body["invoice_status"] == "REJECTED"


async def test_invoice_stays_in_review_until_every_blocking_anomaly_is_resolved(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85, total_amount=1500)
    first, second = invoice["anomalies"]
    assert (await resolve(client, tenant, first["id"])).json()["invoice_status"] == "NEEDS_REVIEW"
    assert (await resolve(client, tenant, second["id"])).json()["invoice_status"] == "APPROVED"


async def test_a_confirmed_anomaly_keeps_the_invoice_rejected(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85, total_amount=1500)
    first, second = invoice["anomalies"]
    assert (await resolve(client, tenant, first["id"], "CONFIRMED")).json()["invoice_status"] == "REJECTED"
    assert (await resolve(client, tenant, second["id"])).json()["invoice_status"] == "REJECTED"


async def test_resolving_twice_conflicts(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85)
    anomaly_id = invoice["anomalies"][0]["id"]
    assert (await resolve(client, tenant, anomaly_id)).status_code == 200
    again = await resolve(client, tenant, anomaly_id, "CONFIRMED")
    assert again.status_code == 409
    assert again.json()["error"]["details"]["resolved_by"] == str(tenant.reviewer_id)


async def test_members_cannot_resolve(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85)
    response = await resolve(client, tenant, invoice["anomalies"][0]["id"], resolved_by=tenant.member_id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_reviewer_must_belong_to_the_organization(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    invoice = await flagged_invoice(client, tenant, confidence=85)
    response = await resolve(client, tenant, invoice["anomalies"][0]["id"], resolved_by=other.reviewer_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"


async def test_cannot_resolve_another_tenants_anomaly(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    invoice = await flagged_invoice(client, other, confidence=85)
    for anomaly_id in (invoice["anomalies"][0]["id"], str(uuid.uuid4())):
        response = await resolve(client, tenant, anomaly_id)
        assert response.status_code == 404


async def test_only_dismissed_or_confirmed_are_accepted(client, tenant):
    invoice = await flagged_invoice(client, tenant, confidence=85)
    response = await resolve(client, tenant, invoice["anomalies"][0]["id"], "OPEN")
    assert response.status_code == 422
