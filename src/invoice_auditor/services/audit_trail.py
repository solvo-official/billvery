"""The invoice's chain of custody (`audit_logs`) and tamper checks on its stored original.

Entries are written in the same transaction as the change they record. The database refuses to
commit a resolution without one (migration 0005), so these helpers are the only way decisions
reach the ledger.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..enums import AuditAction, InvoiceStatus
from ..models import AuditLog, Invoice, User
from ..schemas.api import display_name
from ..storage import DocumentStore

if TYPE_CHECKING:  # pragma: no cover
    from ..db import Database


def audit_entry(
    invoice: Invoice,
    action: AuditAction,
    *,
    user: User | None,
    new_status: InvoiceStatus | str,
    previous_status: InvoiceStatus | str | None = None,
    anomaly_id: UUID | None = None,
    note: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """A custody entry for `invoice`. created_at is left to the database: the commit-time check
    matches entries to decisions by transaction time."""
    return AuditLog(
        organization_id=invoice.organization_id,
        invoice_id=invoice.id,
        anomaly_id=anomaly_id,
        user_id=user.id if user is not None else None,
        user_display_name=display_name(user) if user is not None else None,
        action=action.value,
        resolution_note=note,
        previous_status=str(previous_status) if previous_status is not None else None,
        new_status=str(new_status),
        file_sha256=invoice.file_hash,
        details=details or {},
    )


async def trail(session: AsyncSession, organization_id: UUID, invoice_id: UUID) -> list[AuditLog]:
    """Every entry for one invoice, oldest first."""
    return list(
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == organization_id, AuditLog.invoice_id == invoice_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )


# --- Tamper verification ---------------------------------------------------------------------

IntegrityStatus = Literal["verified", "tampered", "unavailable"]


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    status: IntegrityStatus
    #: invoices.file_hash, what the ledger says the document is
    recorded_sha256: str
    #: the INGESTED entry's hash: written once, never updatable
    ingested_sha256: str | None
    #: SHA-256 of the stored bytes right now; None when this service doesn't hold the original
    computed_sha256: str | None
    computed_size_bytes: int | None
    #: every custody entry carries the same hash as the ledger
    trail_consistent: bool
    checked_at: datetime
    message: str


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def verify_document(
    session: AsyncSession, db: "Database", store: DocumentStore, invoice: Invoice
) -> IntegrityReport:
    """Re-hash the stored original and compare it with the hash recorded at ingestion.

    Three records must agree: the bytes in storage, the invoice row, and the immutable INGESTED
    entry. Any difference means the document or the ledger was changed after the fact.
    """
    entries = await trail(session, invoice.organization_id, invoice.id)
    ingested = next((entry for entry in entries if entry.action == AuditAction.INGESTED), None)
    trail_consistent = all(entry.file_sha256 == invoice.file_hash for entry in entries)

    stored = await store.fetch(db, invoice.source_file_url, invoice.organization_id)
    computed: tuple[str, int] | None
    if isinstance(stored, Path):
        computed = await asyncio.to_thread(_sha256_file, stored)
    elif stored is not None:
        content = stored
        computed = (await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest()), len(content))
    else:
        computed = None

    records_agree = trail_consistent and (ingested is None or ingested.file_sha256 == invoice.file_hash)
    if computed is None:
        status: IntegrityStatus = "unavailable" if records_agree else "tampered"
        message = (
            "The original wasn't uploaded to this service (it came through the API), so only the recorded hashes can be compared. They agree."
            if records_agree
            else "The recorded hashes disagree: the invoice's file hash was changed after ingestion."
        )
    elif computed[0] != invoice.file_hash:
        status, message = "tampered", "The stored document no longer matches the SHA-256 recorded at ingestion."
    elif not records_agree:
        status, message = "tampered", "The document matches the invoice, but not the hash in its ingestion record: the ledger was altered."
    else:
        status, message = "verified", "The stored document is byte-for-byte the file received at ingestion."

    return IntegrityReport(
        status=status,
        recorded_sha256=invoice.file_hash,
        ingested_sha256=ingested.file_sha256 if ingested is not None else None,
        computed_sha256=computed[0] if computed else None,
        computed_size_bytes=computed[1] if computed else None,
        trail_consistent=trail_consistent,
        checked_at=datetime.now(UTC),
        message=message,
    )
