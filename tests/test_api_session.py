"""Browser sign-in: session cookies, the roles they carry, and the Google callback."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from invoice_auditor.api import auth as auth_routes
from invoice_auditor.auth import issue_session
from invoice_auditor.models import Organization, User

from .factories import invoice_payload, process

pytestmark = pytest.mark.integration

ORIGIN = {"Origin": "http://testserver"}


async def user_model(db, user_id) -> User:
    async with db.session() as session:
        return await session.get(User, user_id)


async def sign_in(client, db, settings, user_id) -> None:
    """Give the client the cookie a completed Google sign-in would have set."""
    token, _ = issue_session(settings, await user_model(db, user_id))
    client.cookies.set(settings.session_cookie_name, token)


async def test_session_cookie_authenticates_and_reports_the_signed_in_user(client, db, settings, tenant):
    assert (await client.get("/api/v1/auth/session")).status_code == 401

    await sign_in(client, db, settings, tenant.reviewer_id)
    body = (await client.get("/api/v1/auth/session")).json()
    assert body["method"] == "session"
    assert body["user"]["id"] == str(tenant.reviewer_id)
    assert body["user"]["can_resolve"] is True
    assert body["organization"]["id"] == str(tenant.organization_id)
    # And the cookie authenticates the rest of the API.
    assert (await client.get("/api/v1/invoices")).status_code == 200


async def test_api_key_session_reports_no_user(client, tenant):
    body = (await client.get("/api/v1/auth/session", headers=tenant.headers)).json()
    assert body["method"] == "api_key"
    assert body["user"] is None


async def test_expired_or_forged_cookies_are_refused(client, db, settings, tenant):
    user = await user_model(db, tenant.reviewer_id)
    stale, _ = issue_session(settings, user, now=datetime.now(UTC) - timedelta(hours=settings.session_ttl_hours + 1))
    client.cookies.set(settings.session_cookie_name, stale)
    assert (await client.get("/api/v1/auth/session")).status_code == 401

    client.cookies.set(settings.session_cookie_name, "not.a.jwt")
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_deactivated_account_loses_its_session(client, db, settings, tenant):
    await sign_in(client, db, settings, tenant.reviewer_id)
    assert (await client.get("/api/v1/auth/session")).status_code == 200
    async with db.session() as session:
        await session.execute(update(User).where(User.id == tenant.reviewer_id).values(is_active=False))
        await session.commit()
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_members_may_read_but_not_resolve(client, db, settings, tenant):
    invoice = (await process(client, tenant, invoice_payload(confidence=85))).json()
    await sign_in(client, db, settings, tenant.member_id)
    assert (await client.get("/api/v1/invoices")).status_code == 200
    refused = await client.post(
        f"/api/v1/invoices/{invoice['invoice_id']}/approve",
        json={"approved_by": str(tenant.member_id)},
        headers=ORIGIN,
    )
    assert refused.status_code == 403
    assert "cannot anomalies resolve" in refused.json()["error"]["message"]


async def test_a_signed_in_reviewer_can_only_act_as_themselves(client, db, settings, tenant):
    invoice = (await process(client, tenant, invoice_payload(confidence=85))).json()
    await sign_in(client, db, settings, tenant.reviewer_id)

    someone_else = await client.post(
        f"/api/v1/invoices/{invoice['invoice_id']}/approve",
        json={"approved_by": str(tenant.member_id)},
        headers=ORIGIN,
    )
    assert someone_else.status_code == 403
    assert someone_else.json()["error"]["message"] == "You can only record decisions in your own name."

    themselves = await client.post(
        f"/api/v1/invoices/{invoice['invoice_id']}/approve",
        json={"approved_by": str(tenant.reviewer_id)},
        headers=ORIGIN,
    )
    assert themselves.status_code == 200
    assert themselves.json()["approval"]["by"]["id"] == str(tenant.reviewer_id)


async def test_cookie_writes_need_a_same_origin_request(client, db, settings, tenant):
    invoice = (await process(client, tenant, invoice_payload(confidence=85))).json()
    await sign_in(client, db, settings, tenant.reviewer_id)
    body = {"approved_by": str(tenant.reviewer_id)}

    cross_site = await client.post(
        f"/api/v1/invoices/{invoice['invoice_id']}/approve", json=body, headers={"Origin": "https://evil.example"}
    )
    assert cross_site.status_code == 403
    assert (await client.post(f"/api/v1/invoices/{invoice['invoice_id']}/approve", json=body)).status_code == 403
    assert (await client.post(f"/api/v1/invoices/{invoice['invoice_id']}/approve", json=body, headers=ORIGIN)).status_code == 200


async def test_api_keys_are_unaffected_by_the_origin_rule(client, tenant):
    # Integrations are not browsers: no cookie, no Origin, still allowed.
    assert (await process(client, tenant, invoice_payload())).status_code == 201


async def test_logout_clears_the_cookie(client, db, settings, tenant):
    await sign_in(client, db, settings, tenant.reviewer_id)
    response = await client.post("/api/v1/auth/logout", headers=ORIGIN)
    assert response.status_code == 204
    expiry = response.headers["set-cookie"]
    assert expiry.startswith(f"{settings.session_cookie_name}=") and "Max-Age=0" in expiry
    client.cookies.clear()
    assert (await client.get("/api/v1/auth/session")).status_code == 401


# --- Google sign-in ---------------------------------------------------------------------------


@pytest.fixture
def google(settings):
    settings.google_client_id = "client-id.apps.googleusercontent.com"
    settings.google_client_secret = "client-secret"
    # The test transport speaks http, and a Secure cookie would never come back over it.
    settings.public_url = "http://testserver"
    return settings


async def test_session_cookies_are_secure_on_https_deployments(client, google):
    google.public_url = "https://audit.example.com"
    response = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"].replace("samesite", "SameSite")


async def test_login_redirects_to_google_with_pkce_and_state(client, google):
    response = await client.get("/api/v1/auth/google/login", params={"next": "/#/approved"}, follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in location and "client-id.apps" in location
    assert "redirect_uri=http%3A%2F%2Ftestserver%2Fapi%2Fv1%2Fauth%2Fgoogle%2Fcallback" in location
    assert client.cookies.get(auth_routes.STATE_COOKIE)


async def test_callback_signs_in_a_known_user(client, db, google, tenant, monkeypatch):
    user = await user_model(db, tenant.reviewer_id)

    async def fake_exchange(settings, *, code, verifier, redirect_uri):
        assert code == "auth-code" and verifier and redirect_uri.endswith("/api/v1/auth/google/callback")
        return {"email": user.email, "name": "Jordan Lee", "sub": "google-123"}

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange)
    start = await client.get("/api/v1/auth/google/login", params={"next": "/#/approved"}, follow_redirects=False)
    state = dict(pair.split("=", 1) for pair in start.headers["location"].split("?", 1)[1].split("&"))["state"]

    done = await client.get("/api/v1/auth/google/callback", params={"code": "auth-code", "state": state}, follow_redirects=False)
    assert done.status_code == 303
    assert done.headers["location"] == "http://testserver/#/approved"
    assert client.cookies.get(google.session_cookie_name)
    assert (await client.get("/api/v1/auth/session")).json()["user"]["id"] == str(tenant.reviewer_id)
    # Google's display name fills in a blank profile.
    assert (await user_model(db, tenant.reviewer_id)).full_name == "Jordan Lee"


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"error": "access_denied"}, "cancelled"),
        ({"code": "auth-code", "state": "someone-elses-state"}, "state_mismatch"),
        ({"state": "x"}, "no_code"),
    ],
)
async def test_callback_refuses_bad_returns(client, google, params, reason):
    await client.get("/api/v1/auth/google/login", follow_redirects=False)
    response = await client.get("/api/v1/auth/google/callback", params=params, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"http://testserver/?auth_error={reason}"
    assert not client.cookies.get(google.session_cookie_name)


async def test_callback_gives_a_new_address_its_own_workspace(client, db, google, monkeypatch):
    async def fake_exchange(settings, **_kwargs):
        return {"email": "stranger@example.com", "name": "Stranger", "sub": "google-999"}

    monkeypatch.setattr(auth_routes, "exchange_code", fake_exchange)
    start = await client.get("/api/v1/auth/google/login", follow_redirects=False)
    state = dict(pair.split("=", 1) for pair in start.headers["location"].split("?", 1)[1].split("&"))["state"]
    response = await client.get("/api/v1/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert response.headers["location"] == "http://testserver/"
    me = (await client.get("/api/v1/auth/session")).json()
    assert me["user"]["role"] == "owner" and me["organization"]["name"] == "Stranger's workspace"


async def test_callback_without_a_state_cookie_is_refused(client, google):
    response = await client.get("/api/v1/auth/google/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert response.headers["location"] == "http://testserver/?auth_error=state_expired"


async def test_session_of_another_organizations_user_sees_only_its_own_invoices(client, db, settings, tenant):
    from .factories import make_tenant

    other = await make_tenant(db, name="Other Firm")
    await process(client, tenant, invoice_payload(invoice_number="MINE-1"))
    await sign_in(client, db, settings, other.reviewer_id)
    assert (await client.get("/api/v1/invoices")).json()["items"] == []


async def test_unknown_user_id_in_a_valid_cookie_is_refused(client, db, settings, tenant):
    user = await user_model(db, tenant.reviewer_id)
    user.id = uuid.uuid4()  # a cookie for an account that no longer exists
    token, _ = issue_session(settings, user)
    client.cookies.set(settings.session_cookie_name, token)
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_organization_deactivation_ends_sessions(client, db, settings, tenant):
    await sign_in(client, db, settings, tenant.reviewer_id)
    async with db.session() as session:
        await session.execute(update(Organization).where(Organization.id == tenant.organization_id).values(is_active=False))
        await session.commit()
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_users_are_matched_by_lower_cased_email(db, tenant):
    async with db.session() as session:
        stored = await session.scalar(select(User.email).where(User.id == tenant.reviewer_id))
    assert stored == stored.lower()
