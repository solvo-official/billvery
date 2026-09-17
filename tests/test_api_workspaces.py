"""Workspaces: a private workspace per Google account, sharing by invitation, switching between them."""

import pytest
from sqlalchemy import func, select

from invoice_auditor.api import auth as auth_routes
from invoice_auditor.auth import issue_session
from invoice_auditor.enums import UserRole
from invoice_auditor.models import Organization, User

from .factories import invoice_payload, process

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "http://testserver"}
# What the fake Google token endpoint answers with; set by google_sign_in.
IDENTITY: dict[str, str | None] = {}


@pytest.fixture
def google(settings, monkeypatch):
    """Google sign-in that answers with the identity google_sign_in puts in IDENTITY."""
    settings.google_client_id, settings.google_client_secret, settings.public_url = "cid", "csecret", "http://testserver"

    async def fake_exchange(_settings, **_kwargs):
        return dict(IDENTITY)

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange)
    return settings


async def google_sign_in(client, google, email: str, name: str | None = None):
    IDENTITY.clear()
    IDENTITY.update(email=email.strip().lower(), name=name, sub=f"g-{email}")
    client.cookies.clear()
    start = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = dict(pair.split("=", 1) for pair in start.headers["location"].split("?", 1)[1].split("&"))["state"]
    return await client.get("/api/v1/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)


async def count(db, model) -> int:
    async with db.session() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_first_sign_in_creates_a_private_workspace(client, db, google, tenant):
    await process(client, tenant, invoice_payload(invoice_number="SOMEONE-ELSES-1"))
    organizations_before = await count(db, Organization)

    done = await google_sign_in(client, google, "Sultan.Alt@gmail.com", "Sultan Alt")
    assert done.status_code == 303 and done.headers["location"] == "http://testserver/"
    assert await count(db, Organization) == organizations_before + 1

    me = (await client.get("/api/v1/auth/session")).json()
    assert me["user"]["email"] == "sultan.alt@gmail.com" and me["user"]["role"] == "owner"
    assert me["organization"]["name"] == "Sultan's workspace"
    assert me["organization"]["id"] != str(tenant.organization_id)
    # Nothing from another workspace is visible.
    assert (await client.get("/api/v1/invoices")).json()["items"] == []
    assert [u["email"] for u in (await client.get("/api/v1/users")).json()] == ["sultan.alt@gmail.com"]

    # Signing in again returns to the same workspace instead of creating another.
    await google_sign_in(client, google, "sultan.alt@gmail.com", "Sultan Alt")
    assert await count(db, Organization) == organizations_before + 1


async def test_invited_account_lands_in_the_inviting_workspace_without_a_new_one(client, db, google, settings, tenant):
    async with db.session() as session:
        reviewer = await session.get(User, tenant.reviewer_id)
    token, _ = issue_session(settings, reviewer)
    client.cookies.set(settings.session_cookie_name, token)
    async with db.session() as session:
        await session.execute(User.__table__.update().where(User.id == tenant.reviewer_id).values(role=UserRole.OWNER.value))
        await session.commit()
    assert (await client.post("/api/v1/users", json={"email": "teammate@gmail.com", "role": "reviewer"}, headers=ORIGIN)).status_code == 201
    organizations_before = await count(db, Organization)

    await google_sign_in(client, google, "teammate@gmail.com", "Team Mate")
    me = (await client.get("/api/v1/auth/session")).json()
    assert me["organization"]["id"] == str(tenant.organization_id) and me["user"]["role"] == "reviewer"
    assert await count(db, Organization) == organizations_before


async def test_switching_between_own_and_shared_workspaces(client, db, google, tenant):
    await process(client, tenant, invoice_payload(invoice_number="SHARED-1"))
    await google_sign_in(client, google, "alt@gmail.com", "Alt")
    own = (await client.get("/api/v1/auth/session")).json()["organization"]["id"]

    # The shared workspace invites the same Google account.
    async with db.session() as session:
        session.add(User(organization_id=tenant.organization_id, email="alt@gmail.com", role=UserRole.MEMBER.value))
        await session.commit()

    workspaces = (await client.get("/api/v1/auth/workspaces")).json()
    assert {w["organization"]["id"]: (w["role"], w["current"]) for w in workspaces} == {
        own: ("owner", True),
        str(tenant.organization_id): ("member", False),
    }

    switched = await client.post("/api/v1/auth/workspaces/switch", json={"organization_id": str(tenant.organization_id)}, headers=ORIGIN)
    assert switched.status_code == 200 and switched.json()["user"]["role"] == "member"
    assert [i["invoice_number"] for i in (await client.get("/api/v1/invoices")).json()["items"]] == ["SHARED-1"]

    # A later sign-in resumes the workspace used most recently.
    await google_sign_in(client, google, "alt@gmail.com", "Alt")
    assert (await client.get("/api/v1/auth/session")).json()["organization"]["id"] == str(tenant.organization_id)

    back = await client.post("/api/v1/auth/workspaces/switch", json={"organization_id": own}, headers=ORIGIN)
    assert back.status_code == 200
    assert (await client.get("/api/v1/invoices")).json()["items"] == []


async def test_cannot_switch_into_a_workspace_without_access(client, db, google, tenant):
    await google_sign_in(client, google, "outsider@gmail.com")
    refused = await client.post("/api/v1/auth/workspaces/switch", json={"organization_id": str(tenant.organization_id)}, headers=ORIGIN)
    assert refused.status_code == 404
    assert (await client.get("/api/v1/invoices")).json()["items"] == []

    # Removed access is no access.
    async with db.session() as session:
        session.add(User(organization_id=tenant.organization_id, email="outsider@gmail.com", role=UserRole.MEMBER.value, is_active=False))
        await session.commit()
    refused = await client.post("/api/v1/auth/workspaces/switch", json={"organization_id": str(tenant.organization_id)}, headers=ORIGIN)
    assert refused.status_code == 404


async def test_creating_more_workspaces_is_capped(client, google):
    google.max_workspaces_per_account = 2
    await google_sign_in(client, google, "builder@gmail.com", "Builder")
    second = await client.post("/api/v1/auth/workspaces", json={"name": "Side project"}, headers=ORIGIN)
    assert second.status_code == 201 and second.json()["organization"]["name"] == "Side project"
    assert (await client.get("/api/v1/auth/session")).json()["organization"]["name"] == "Side project"
    third = await client.post("/api/v1/auth/workspaces", json={}, headers=ORIGIN)
    assert third.status_code == 409


async def test_signup_allowlist_closes_open_sign_up(client, db, google):
    google.signup_allowlist = "boss@gmail.com, @billvery.com"
    organizations_before = await count(db, Organization)
    refused = await google_sign_in(client, google, "stranger@gmail.com")
    assert refused.headers["location"] == "http://testserver/?auth_error=signup_closed"
    assert await count(db, Organization) == organizations_before
    assert (await google_sign_in(client, google, "ali@billvery.com")).headers["location"] == "http://testserver/"
    assert (await google_sign_in(client, google, "boss@gmail.com")).headers["location"] == "http://testserver/"


async def test_owners_rename_and_localize_their_workspace(client, google):
    await google_sign_in(client, google, "owner@gmail.com", "Owner")
    updated = await client.patch(
        "/api/v1/organization",
        json={"name": "Billvery Karachi", "currency_code": "pkr", "country_code": "pk", "legal_name": ""},
        headers=ORIGIN,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["name"], body["currency_code"], body["country_code"], body["legal_name"]) == ("Billvery Karachi", "PKR", "PK", None)
    assert (await client.patch("/api/v1/organization", json={"currency_code": "RUPEES"}, headers=ORIGIN)).status_code == 422
    assert (await client.patch("/api/v1/organization", json={"name": ""}, headers=ORIGIN)).status_code == 422
    kept = await client.patch("/api/v1/organization", json={"name": None, "currency_code": None}, headers=ORIGIN)
    assert kept.status_code == 200 and (kept.json()["name"], kept.json()["currency_code"]) == ("Billvery Karachi", "PKR")


async def test_members_cannot_change_workspace_settings(client, db, settings, tenant):
    async with db.session() as session:
        member = await session.get(User, tenant.member_id)
    token, _ = issue_session(settings, member)
    client.cookies.set(settings.session_cookie_name, token)
    assert (await client.patch("/api/v1/organization", json={"name": "Hijacked"}, headers=ORIGIN)).status_code == 403
