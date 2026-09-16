from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from invoice_auditor.models import ApiKey, Organization

from .factories import invoice_payload, make_tenant, process

pytestmark = pytest.mark.integration


async def test_missing_key(client):
    response = await client.post("/api/v1/invoices/process", json=invoice_payload())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


async def test_unknown_key(client):
    response = await client.get(
        "/api/v1/invoices/00000000-0000-0000-0000-000000000000/audit",
        headers={"Authorization": "Bearer iak_not-a-real-key"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"revoked_at": datetime.now(UTC)},
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)},
    ],
)
async def test_revoked_or_expired_key(client, tenant, db, change):
    async with db.session() as session:
        await session.execute(update(ApiKey).where(ApiKey.id == tenant.api_key_id).values(**change))
        await session.commit()
    assert (await process(client, tenant, invoice_payload())).status_code == 401


async def test_inactive_organization(client, tenant, db):
    async with db.session() as session:
        await session.execute(
            update(Organization).where(Organization.id == tenant.organization_id).values(is_active=False)
        )
        await session.commit()
    assert (await process(client, tenant, invoice_payload())).status_code == 401


async def test_scopes_are_enforced(client, db):
    read_only = await make_tenant(db, scopes=["invoices:read"])
    response = await process(client, read_only, invoice_payload())
    assert response.status_code == 403
    assert "invoices:write" in response.json()["error"]["message"]


async def test_last_used_at_is_recorded(client, tenant, db):
    await process(client, tenant, invoice_payload())
    async with db.session() as session:
        api_key = await session.get(ApiKey, tenant.api_key_id)
    assert api_key.last_used_at is not None


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
