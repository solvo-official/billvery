"""Guarantees enforced by the database itself, independent of the application code."""

import asyncio
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from invoice_auditor.cli import alembic_config
from invoice_auditor.models import AnomalyLog, Base, Invoice, InvoiceItem, Organization, User, Vendor

from .conftest import create_database, drop_database
from .factories import invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration


async def flagged(client, tenant):
    body = (await process(client, tenant, invoice_payload(confidence=85))).json()
    return uuid.UUID(body["invoice_id"]), uuid.UUID(body["anomalies"][0]["id"])


async def test_findings_cannot_be_edited(client, tenant, db):
    _, anomaly_id = await flagged(client, tenant)
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="immutable"):
            await session.execute(update(AnomalyLog).where(AnomalyLog.id == anomaly_id).values(severity="LOW"))


async def test_resolutions_are_final(client, tenant, db):
    _, anomaly_id = await flagged(client, tenant)
    await client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"resolution": "DISMISSED", "resolved_by": str(tenant.reviewer_id)},
        headers=tenant.headers,
    )
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="already resolved"):
            await session.execute(
                update(AnomalyLog).where(AnomalyLog.id == anomaly_id).values(resolution_note="edited later")
            )


async def test_resolution_fields_must_be_consistent(client, tenant, db):
    _, anomaly_id = await flagged(client, tenant)
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="resolution_consistent"):
            await session.execute(update(AnomalyLog).where(AnomalyLog.id == anomaly_id).values(status="DISMISSED"))


async def test_findings_cannot_be_deleted(client, tenant, db):
    _, anomaly_id = await flagged(client, tenant)
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(delete(AnomalyLog).where(AnomalyLog.id == anomaly_id))


async def test_invoice_with_findings_cannot_be_deleted(client, tenant, db):
    invoice_id, _ = await flagged(client, tenant)
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="fk_anomaly_logs_invoice_id_organization_id_invoices"):
            await session.execute(delete(Invoice).where(Invoice.id == invoice_id))


async def test_vendor_with_invoices_cannot_be_deleted(client, tenant, db):
    await process(client, tenant, invoice_payload())
    async with db.session() as session:
        with pytest.raises(IntegrityError, match="fk_invoices_vendor_id_organization_id_vendors"):
            await session.execute(delete(Vendor))


async def test_deleting_a_tenant_removes_all_of_its_data(client, tenant, db):
    other = await make_tenant(db, name="Other Firm")
    invoice_id, anomaly_id = await flagged(client, tenant)
    await client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"resolution": "CONFIRMED", "resolved_by": str(tenant.reviewer_id)},
        headers=tenant.headers,
    )
    await process(client, other, invoice_payload())

    async with db.session() as session:
        await session.execute(delete(Organization).where(Organization.id == tenant.organization_id))
        await session.commit()
        remaining = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (Invoice, InvoiceItem, AnomalyLog, Vendor, User)
        }
    # Only the other tenant's rows are left: 1 invoice, 2 items, its vendor and 2 users.
    assert remaining == {"invoices": 1, "invoice_items": 2, "anomaly_logs": 0, "vendors": 1, "users": 2}


async def test_child_rows_cannot_point_at_another_tenant(client, tenant, db):
    invoice_id, _ = await flagged(client, tenant)
    other = await make_tenant(db, name="Other Firm")
    async with db.session() as session:
        session.add(
            AnomalyLog(
                invoice_id=invoice_id,
                organization_id=other.organization_id,
                anomaly_type="PRICE_SPIKE",
                severity="HIGH",
                confidence_score=90,
                description="forged",
            )
        )
        with pytest.raises(IntegrityError, match="fk_anomaly_logs_invoice_id_organization_id_invoices"):
            await session.flush()


async def test_updated_at_is_maintained_by_trigger(client, tenant, db):
    async with db.session() as session:
        before = await session.scalar(select(Organization.updated_at).where(Organization.id == tenant.organization_id))
        await asyncio.sleep(0.01)
        await session.execute(text("UPDATE organizations SET name = 'Renamed' WHERE id = :id"), {"id": tenant.organization_id})
        await session.commit()
        after = await session.scalar(select(Organization.updated_at).where(Organization.id == tenant.organization_id))
    assert after > before


async def test_migration_matches_models(database_url):
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata
                )
            )
    finally:
        await engine.dispose()
    assert differences == []


def test_migration_downgrades_and_upgrades_cleanly(database_url):
    name, url = create_database()
    try:
        config = alembic_config(url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        drop_database(name)
