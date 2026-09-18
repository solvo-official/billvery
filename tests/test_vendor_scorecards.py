"""Vendor risk scorecards: aggregation over the ledger (integration) and the tier rules (unit)."""

import pytest

from invoice_auditor.config import Settings
from invoice_auditor.enums import VendorRiskTier
from invoice_auditor.services.vendor_risk import VendorMetrics, assess

from .factories import invoice_payload, process

SETTINGS = Settings(_env_file=None, database_url="postgresql+asyncpg://unused/unused")


def metrics(total, held=0, duplicates=0, confirmed=0, rejected=0):
    return VendorMetrics(
        total_invoices=total,
        held_for_review=held,
        duplicate_invoices=duplicates,
        confirmed_duplicates=confirmed,
        rejected_invoices=rejected,
        in_review=0,
    )


# --- Tier rules ---------------------------------------------------------------------------------


def test_clean_history_is_low():
    risk = assess(metrics(10, held=1), SETTINGS)
    assert risk.tier is VendorRiskTier.LOW
    assert risk.reasons == ["10% of invoices held for review, no duplicates"]


def test_a_rejected_invoice_makes_a_vendor_high_risk():
    risk = assess(metrics(20, held=1, rejected=1), SETTINGS)
    assert risk.tier is VendorRiskTier.HIGH
    assert "rejected" in risk.reasons[0]


def test_confirmed_duplicate_is_high():
    assert assess(metrics(20, held=1, duplicates=1, confirmed=1), SETTINGS).tier is VendorRiskTier.HIGH


def test_flag_rate_thresholds():
    assert assess(metrics(10, held=2), SETTINGS).tier is VendorRiskTier.MEDIUM  # 20%
    assert assess(metrics(10, held=5), SETTINGS).tier is VendorRiskTier.HIGH  # 50%


def test_short_history_cannot_reach_high_on_rates_alone():
    risk = assess(metrics(1, held=1), SETTINGS)  # 100%, but one invoice
    assert risk.tier is VendorRiskTier.MEDIUM
    assert risk.reasons[-1] == "Limited history: 1 invoice"


def test_unconfirmed_duplicates_are_medium_and_frequent_ones_high():
    assert assess(metrics(10, duplicates=1), SETTINGS).tier is VendorRiskTier.MEDIUM
    assert assess(metrics(8, duplicates=2), SETTINGS).tier is VendorRiskTier.HIGH  # 25%


# --- Aggregation ----------------------------------------------------------------------------------


async def scorecards(client, tenant):
    response = await client.get("/api/v1/vendors/scorecards", headers=tenant.headers)
    assert response.status_code == 200, response.text
    return {card["name"]: card for card in response.json()}


@pytest.mark.integration
async def test_scorecards_aggregate_each_vendors_history(client, tenant):
    for number in ("C-1", "C-2", "C-3"):
        await process(client, tenant, invoice_payload(vendor_name="Clean Supplies", vendor_tax_id="111", invoice_number=number))
    # Risky: one held for review, then a CONFIRMED finding rejects it; one re-submitted number.
    risky = {"vendor_name": "Risky Traders", "vendor_tax_id": "222"}
    held = (await process(client, tenant, invoice_payload(**risky, invoice_number="R-1", confidence=85))).json()
    await client.post(
        f"/api/v1/anomalies/{held['anomalies'][0]['id']}/resolve",
        json={"resolution": "CONFIRMED", "resolved_by": str(tenant.reviewer_id), "note": "Not ours"},
        headers=tenant.headers,
    )
    await process(client, tenant, invoice_payload(**risky, invoice_number="R-2"))
    await process(client, tenant, invoice_payload(**risky, invoice_number="R-2"))  # duplicate number

    cards = await scorecards(client, tenant)
    clean, risky_card = cards["Clean Supplies"], cards["Risky Traders"]
    assert (clean["total_invoices"], clean["held_for_review"], clean["flag_rate_percent"]) == (3, 0, 0.0)
    assert clean["risk"]["tier"] == "LOW"

    assert risky_card["total_invoices"] == 3
    assert risky_card["rejected_invoices"] == 1
    assert risky_card["duplicate_invoices"] == 1
    assert risky_card["held_for_review"] == 2  # low confidence, then the duplicate number (HIGH)
    assert risky_card["flag_rate_percent"] == 66.7
    assert risky_card["risk"]["tier"] == "HIGH"
    # Riskiest first.
    response = await client.get("/api/v1/vendors/scorecards", headers=tenant.headers)
    assert [card["name"] for card in response.json()] == ["Risky Traders", "Clean Supplies"]


@pytest.mark.integration
async def test_scorecards_can_be_filtered_by_vendor(client, tenant):
    body = (await process(client, tenant, invoice_payload(vendor_name="Only Me", vendor_tax_id="333"))).json()
    await process(client, tenant, invoice_payload(vendor_name="Someone Else", vendor_tax_id="444", invoice_number="X-9"))
    vendor_id = body["vendor_on_file"]["id"]
    response = await client.get("/api/v1/vendors/scorecards", params={"vendor_id": vendor_id}, headers=tenant.headers)
    assert [card["vendor_id"] for card in response.json()] == [vendor_id]
