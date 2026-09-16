import copy
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from invoice_auditor.db import Database
from invoice_auditor.enums import ApiScope, UserRole
from invoice_auditor.models import ApiKey, Organization, User
from invoice_auditor.security import generate_api_key


@dataclass(frozen=True)
class Tenant:
    organization_id: uuid.UUID
    reviewer_id: uuid.UUID
    member_id: uuid.UUID
    api_key: str
    api_key_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


async def make_tenant(
    db: Database,
    *,
    name: str = "Acme Accounting",
    country_code: str | None = None,
    currency_code: str = "USD",
    settings: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
) -> Tenant:
    async with db.session() as session:
        organization = Organization(
            name=name, country_code=country_code, currency_code=currency_code, settings=settings or {}
        )
        session.add(organization)
        await session.flush()
        reviewer = User(
            organization_id=organization.id,
            email=f"reviewer-{secrets.token_hex(4)}@example.com",
            role=UserRole.REVIEWER.value,
        )
        member = User(
            organization_id=organization.id,
            email=f"member-{secrets.token_hex(4)}@example.com",
            role=UserRole.MEMBER.value,
        )
        key, prefix, key_hash = generate_api_key()
        api_key = ApiKey(
            organization_id=organization.id,
            name="test",
            key_prefix=prefix,
            key_hash=key_hash,
            permissions={"scopes": scopes if scopes is not None else [s.value for s in ApiScope]},
        )
        session.add_all([reviewer, member, api_key])
        await session.commit()
        return Tenant(organization.id, reviewer.id, member.id, key, api_key.id)


def file_metadata(content: bytes | None = None) -> dict[str, Any]:
    content = content if content is not None else secrets.token_bytes(64)
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(content),
        "storage_url": "s3://invoices/invoice.pdf",
    }


BASE_EXTRACTION: dict[str, Any] = {
    "vendor_name": "ABC Trading LLC",
    "vendor_tax_id": "100-200-300-400-003",
    "vendor_confidence": 97,
    "invoice_number": "INV-1001",
    "currency": "USD",
    "subtotal": 1000,
    "tax_amount": 50,
    "shipping_amount": 0,
    "discount_amount": 0,
    "total_amount": 1050,
    "line_items": [
        {"description": "Laptop stand", "quantity": 2, "unit_price": 250, "line_total": 500},
        {"description": "Monitor arm", "quantity": 1, "unit_price": 500, "line_total": 500},
    ],
    "confidence": 95,
}


def invoice_payload(*, content: bytes | None = None, **extraction: Any) -> dict[str, Any]:
    """A clean, balanced invoice unless overridden. Every call uses a new file unless `content` is given."""
    today = datetime.now(UTC).date()
    body = copy.deepcopy(BASE_EXTRACTION) | {
        "invoice_date": (today - timedelta(days=10)).isoformat(),
        "due_date": (today + timedelta(days=20)).isoformat(),
    }
    body.update(extraction)
    return {"file": file_metadata(content), "extraction": body, "metadata": {"department": "Operations"}}


def single_line(description: str, unit_price: float, *, quantity: int = 1, tax: float = 0) -> dict[str, Any]:
    """Extraction overrides for a balanced one-line invoice."""
    subtotal = unit_price * quantity
    return {
        "line_items": [{"description": description, "quantity": quantity, "unit_price": unit_price}],
        "subtotal": subtotal,
        "tax_amount": tax,
        "total_amount": subtotal + tax,
    }


async def process(
    client: httpx.AsyncClient, tenant: Tenant, payload: dict[str, Any], **headers: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/invoices/process", json=payload, headers=tenant.headers | {k.replace("_", "-"): v for k, v in headers.items()}
    )


def anomaly_types(body: dict[str, Any]) -> dict[str, str]:
    """{anomaly type: severity} for an audit response."""
    return {anomaly["type"]: anomaly["severity"] for anomaly in body["anomalies"]}
