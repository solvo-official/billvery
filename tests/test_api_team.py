"""Team management from the console: invite Google accounts, change roles, remove access."""

import secrets

import pytest

from invoice_auditor.api import auth as auth_routes
from invoice_auditor.auth import issue_session
from invoice_auditor.enums import UserRole
from invoice_auditor.models import User

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "http://testserver"}


async def add_user(db, tenant, role: UserRole, email: str | None = None) -> User:
    async with db.session() as session:
        user = User(
            organization_id=tenant.organization_id,
            email=email or f"{role.value}-{secrets.token_hex(4)}@example.com",
            role=role.value,
        )
        session.add(user)
        await session.commit()
        return user


async def sign_in_as(client, settings, user: User) -> None:
    client.cookies.clear()
    token, _ = issue_session(settings, user)
    client.cookies.set(settings.session_cookie_name, token)


async def invite(client, email, role="reviewer", **extra):
    return await client.post("/api/v1/users", json={"email": email, "role": role, **extra}, headers=ORIGIN)


async def update(client, user_id, **changes):
    return await client.patch(f"/api/v1/users/{user_id}", json=changes, headers=ORIGIN)


async def test_owner_invites_a_google_account_that_can_then_sign_in(client, db, settings, tenant, monkeypatch):
    owner = await add_user(db, tenant, UserRole.OWNER)
    await sign_in_as(client, settings, owner)

    response = await invite(client, "  New.Reviewer@Gmail.com ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "new.reviewer@gmail.com"  # normalized
    assert body["role"] == "reviewer" and body["active"] is True and body["last_login_at"] is None

    listed = (await client.get("/api/v1/users")).json()
    assert "new.reviewer@gmail.com" in {user["email"] for user in listed}

    # The invited address can now complete Google sign-in, which records the sign-in time.
    settings.google_client_id, settings.google_client_secret, settings.public_url = "cid", "csecret", "http://testserver"

    async def fake_exchange(settings, **_kwargs):
        return {"email": "new.reviewer@gmail.com", "name": "New Reviewer", "sub": "g-1"}

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange)
    client.cookies.clear()
    start = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = dict(pair.split("=", 1) for pair in start.headers["location"].split("?", 1)[1].split("&"))["state"]
    done = await client.get("/api/v1/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.headers["location"] == "http://testserver/"
    me = (await client.get("/api/v1/auth/session")).json()["user"]
    assert me["email"] == "new.reviewer@gmail.com" and me["name"] == "New Reviewer" and me["last_login_at"] is not None


async def test_invites_are_validated_and_deduplicated(client, db, settings, tenant):
    admin = await add_user(db, tenant, UserRole.ADMIN)
    await sign_in_as(client, settings, admin)
    assert (await invite(client, "not-an-email")).status_code == 422
    assert (await invite(client, "someone@gmail.com", role="superuser")).status_code == 422
    assert (await invite(client, "someone@gmail.com")).status_code == 201
    duplicate = await invite(client, "SOMEONE@gmail.com")
    assert duplicate.status_code == 409
    assert "already on the team" in duplicate.json()["error"]["message"]


async def test_only_signed_in_owners_and_admins_manage_the_team(client, db, settings, tenant):
    reviewer = await add_user(db, tenant, UserRole.REVIEWER)
    await sign_in_as(client, settings, reviewer)
    refused = await invite(client, "x@gmail.com")
    assert refused.status_code == 403 and "owners and admins" in refused.json()["error"]["message"]

    client.cookies.clear()
    by_key = await client.post("/api/v1/users", json={"email": "x@gmail.com"}, headers=tenant.headers)
    assert by_key.status_code == 403 and "Sign in with Google" in by_key.json()["error"]["message"]


async def test_admins_cannot_create_or_change_owners(client, db, settings, tenant):
    owner = await add_user(db, tenant, UserRole.OWNER)
    admin = await add_user(db, tenant, UserRole.ADMIN)
    await sign_in_as(client, settings, admin)
    assert (await invite(client, "boss@gmail.com", role="owner")).status_code == 403
    assert (await update(client, owner.id, active=False)).status_code == 403
    member = (await invite(client, "member@gmail.com", role="member")).json()
    assert (await update(client, member["id"], role="owner")).status_code == 403
    promoted = await update(client, member["id"], role="reviewer")
    assert promoted.status_code == 200 and promoted.json()["can_resolve"] is True


async def test_nobody_can_change_their_own_access(client, db, settings, tenant):
    owner = await add_user(db, tenant, UserRole.OWNER)
    await sign_in_as(client, settings, owner)
    myself = await update(client, owner.id, role="member")
    assert myself.status_code == 403 and "your own" in myself.json()["error"]["message"]

    second = (await invite(client, "second-owner@gmail.com", role="owner")).json()
    # Two owners: the second may be demoted, which would leave the signed-in owner as the last one.
    assert (await update(client, second["id"], role="admin")).status_code == 200

    other_owner = await add_user(db, tenant, UserRole.OWNER)
    await sign_in_as(client, settings, other_owner)
    assert (await update(client, owner.id, active=False)).status_code == 200  # other_owner remains
    await sign_in_as(client, settings, owner)
    assert (await client.get("/api/v1/auth/session")).status_code == 401  # removed access ends the session


async def test_removing_and_restoring_access(client, db, settings, tenant):
    owner = await add_user(db, tenant, UserRole.OWNER)
    await sign_in_as(client, settings, owner)
    invited = (await invite(client, "temp@gmail.com", role="member")).json()

    removed = await update(client, invited["id"], active=False)
    assert removed.status_code == 200 and removed.json()["active"] is False
    assert "temp@gmail.com" not in {u["email"] for u in (await client.get("/api/v1/users")).json()}
    everyone = (await client.get("/api/v1/users", params={"include_inactive": "true"})).json()
    assert {u["email"]: u["active"] for u in everyone}["temp@gmail.com"] is False

    reinvite = await invite(client, "temp@gmail.com")
    assert reinvite.status_code == 409 and reinvite.json()["error"]["details"]["active"] is False
    restored = await update(client, invited["id"], active=True)
    assert restored.status_code == 200 and restored.json()["active"] is True


async def test_team_changes_need_a_same_origin_request(client, db, settings, tenant):
    owner = await add_user(db, tenant, UserRole.OWNER)
    await sign_in_as(client, settings, owner)
    cross_site = await client.post("/api/v1/users", json={"email": "evil@gmail.com"}, headers={"Origin": "https://evil.example"})
    assert cross_site.status_code == 403
