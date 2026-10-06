from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from invoice_auditor.api.deps import get_session
from invoice_auditor.api.organization import get_manager, update_organization
from invoice_auditor.config import Settings
from invoice_auditor.errors import BadRequest
from invoice_auditor.main import create_app
from invoice_auditor.models import Organization, User
from invoice_auditor.schemas.api import ALLOWED_CURRENCIES, UpdateOrganizationRequest


def test_allowed_currencies_contains_whitelisted_set():
    assert ALLOWED_CURRENCIES == frozenset({"PKR", "USD", "EUR", "GBP", "AED"})


def test_update_organization_request_normalization():
    req = UpdateOrganizationRequest(currency_code="pkr", country_code="pk")
    assert req.currency_code == "PKR"
    assert req.country_code == "PK"

    req_whitespace = UpdateOrganizationRequest(currency_code="  usd  ", country_code="  us  ")
    assert req_whitespace.currency_code == "USD"
    assert req_whitespace.country_code == "US"


@pytest.mark.parametrize("valid_currency", ["PKR", "USD", "EUR", "GBP", "AED", "pkr", "usd", "eur", "gbp", "aed"])
async def test_update_organization_accepts_whitelisted_currencies(valid_currency):
    org_id = uuid4()
    org = Organization(id=org_id, name="Test Org", currency_code="USD", country_code="US", subscription_plan="starter")
    manager = User(id=uuid4(), organization_id=org_id, role="owner", email="owner@test.com")

    session = AsyncMock()
    session.get.return_value = org

    payload = UpdateOrganizationRequest(currency_code=valid_currency)
    result = await update_organization(payload, manager, session)

    assert result.currency_code == valid_currency.upper()
    assert org.currency_code == valid_currency.upper()
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("invalid_currency", ["XYZ", "RUPEES", "INVALID", "", "123", "!@#", "CAD", "AUD", "INR"])
async def test_update_organization_rejects_invalid_currencies_with_bad_request(invalid_currency):
    org_id = uuid4()
    org = Organization(id=org_id, name="Test Org", currency_code="USD", country_code="US")
    manager = User(id=uuid4(), organization_id=org_id, role="owner", email="owner@test.com")

    session = AsyncMock()
    session.get.return_value = org

    payload = UpdateOrganizationRequest(currency_code=invalid_currency)
    with pytest.raises(BadRequest) as exc_info:
        await update_organization(payload, manager, session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "bad_request"
    assert "Unsupported currency" in exc_info.value.message


async def test_update_organization_preserves_currency_when_none():
    org_id = uuid4()
    org = Organization(id=org_id, name="Test Org", currency_code="PKR", country_code="PK", subscription_plan="starter")
    manager = User(id=uuid4(), organization_id=org_id, role="owner", email="owner@test.com")

    session = AsyncMock()
    session.get.return_value = org

    payload = UpdateOrganizationRequest(name="New Org Name", currency_code=None)
    result = await update_organization(payload, manager, session)

    assert result.name == "New Org Name"
    assert result.currency_code == "PKR"
    assert org.currency_code == "PKR"
    session.commit.assert_awaited_once()


@pytest.fixture
def mock_app():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://dummy:dummy@localhost:5432/dummy",
        jwt_secret="dummy-secret-that-is-long-enough-to-sign-tokens-12345",
    )
    application = create_app(settings)

    org_id = uuid4()
    org = Organization(id=org_id, name="Demo Workspace", currency_code="USD", country_code="US", subscription_plan="starter")
    manager = User(id=uuid4(), organization_id=org_id, role="owner", email="owner@example.com")

    session = AsyncMock()
    session.get.return_value = org

    application.dependency_overrides[get_manager] = lambda: manager
    application.dependency_overrides[get_session] = lambda: session

    return application, org


@pytest.mark.parametrize("invalid_currency", ["RUPEES", "XYZ", "INVALID", "", "123", "!@#"])
async def test_endpoint_returns_400_for_invalid_currencies(mock_app, invalid_currency):
    application, _ = mock_app
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        response = await client.patch("/api/v1/organization", json={"currency_code": invalid_currency})
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "bad_request"
        assert "Unsupported currency" in body["error"]["message"]


@pytest.mark.parametrize("allowed_currency", ["PKR", "USD", "EUR", "GBP", "AED", "pkr", "usd", "eur", "gbp", "aed"])
async def test_endpoint_returns_200_for_allowed_currencies(mock_app, allowed_currency):
    application, org = mock_app
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        response = await client.patch("/api/v1/organization", json={"currency_code": allowed_currency})
        assert response.status_code == 200
        body = response.json()
        assert body["currency_code"] == allowed_currency.upper()
        assert org.currency_code == allowed_currency.upper()


async def test_endpoint_preserves_currency_when_null_or_omitted(mock_app):
    application, org = mock_app
    org.currency_code = "PKR"
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        # Currency null -> kept
        res1 = await client.patch("/api/v1/organization", json={"name": "New Name", "currency_code": None})
        assert res1.status_code == 200
        assert res1.json()["name"] == "New Name"
        assert res1.json()["currency_code"] == "PKR"
        assert org.currency_code == "PKR"

        # Currency omitted -> kept
        res2 = await client.patch("/api/v1/organization", json={"legal_name": "New Legal Name"})
        assert res2.status_code == 200
        assert res2.json()["legal_name"] == "New Legal Name"
        assert res2.json()["currency_code"] == "PKR"
        assert org.currency_code == "PKR"


async def test_endpoint_validates_country_code(mock_app):
    application, org = mock_app
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        # Valid country code in lower case is normalized to upper case
        res = await client.patch("/api/v1/organization", json={"country_code": "pk"})
        assert res.status_code == 200
        assert res.json()["country_code"] == "PK"
        assert org.country_code == "PK"

        # Valid country code in upper case
        res_us = await client.patch("/api/v1/organization", json={"country_code": "US"})
        assert res_us.status_code == 200
        assert res_us.json()["country_code"] == "US"
        assert org.country_code == "US"

        # Country code null -> clears country
        res_null = await client.patch("/api/v1/organization", json={"country_code": None})
        assert res_null.status_code == 200
        assert res_null.json()["country_code"] is None
        assert org.country_code is None

        # Invalid country code (not 2 letters) returns 422 validation error
        res_invalid = await client.patch("/api/v1/organization", json={"country_code": "PAKISTAN"})
        assert res_invalid.status_code == 422


async def test_endpoint_rejects_empty_name(mock_app):
    application, _ = mock_app
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        response = await client.patch("/api/v1/organization", json={"name": ""})
        assert response.status_code == 422
