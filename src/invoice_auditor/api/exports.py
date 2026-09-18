import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..enums import ApiScope, InvoiceStatus
from ..errors import InvalidReference, NotFound, PayloadTooLarge
from ..models import Invoice, Organization
from ..schemas.api import ErrorResponse, ExportBatchRequest
from ..services.erp_export import accounting_csv, erp_payload
from .deps import Principal, SessionDep, SettingsDep, require_scope
from .invoices import document_store, serialize

router = APIRouter(prefix="/api/v1/invoices", tags=["export"])

FORMATS = {
    "accounting_csv": ("text/csv; charset=utf-8", "quickbooks-xero.csv"),
    "erp_json": ("application/json", "sap-netsuite.json"),
}


@router.post(
    "/export",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}, "application/json": {}}, "description": "The batch file, as an attachment."},
        413: {"model": ErrorResponse, "description": "More invoices than one batch may hold."},
        422: {"model": ErrorResponse, "description": "A selected invoice doesn't exist here or is still processing."},
    },
)
async def export_batch(
    payload: ExportBatchRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))],
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Export a batch of audited invoices for an accounting system, oldest first.

    `accounting_csv` is the QuickBooks / Xero bill-import layout, one row per line item.
    `erp_json` is a vendor-bill batch for SAP / NetSuite integrations, with control totals.
    Invoices still processing are never exported. The response carries `X-Export-Count` and
    `X-Export-Batch-Id` (also inside the JSON) for reconciliation.
    """
    organization = await session.get(Organization, principal.organization_id)
    if organization is None:
        raise NotFound("Organization not found.")

    stmt = select(Invoice).where(
        Invoice.organization_id == principal.organization_id, Invoice.status != InvoiceStatus.PROCESSING.value
    )
    requested = set(payload.invoice_ids)
    if payload.scope == "approved":
        stmt = stmt.where(Invoice.status == InvoiceStatus.APPROVED.value)
    elif payload.scope == "selected":
        stmt = stmt.where(Invoice.id.in_(requested))

    count = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if count > settings.export_max_invoices:
        raise PayloadTooLarge(
            f"This batch has {count:,} invoices; one export holds up to {settings.export_max_invoices:,}. "
            "Export approved bills only, or select fewer rows.",
            details={"count": count, "limit": settings.export_max_invoices},
        )

    invoices = list(
        (
            await session.scalars(
                stmt.options(selectinload(Invoice.anomalies), selectinload(Invoice.items)).order_by(Invoice.created_at, Invoice.id)
            )
        ).unique()
    )
    missing = requested - {invoice.id for invoice in invoices}
    if missing:
        raise InvalidReference(
            f"{len(missing)} selected invoice{'s' if len(missing) != 1 else ''} weren't found or are still processing.",
            details={"missing": sorted(str(invoice_id) for invoice_id in missing)[:50]},
        )

    records = await serialize(session, invoices, document_store(request))
    batch_id = uuid.uuid4()
    generated_at = datetime.now(UTC)
    media_type, suffix = FORMATS[payload.format]
    if payload.format == "accounting_csv":
        content = accounting_csv(records)
    else:
        batch = erp_payload(records, organization, scope=payload.scope, batch_id=batch_id, generated_at=generated_at)
        content = json.dumps(batch, indent=2, ensure_ascii=False)
    filename = f"billvery-{payload.scope}-{generated_at:%Y%m%d-%H%M%S}-{suffix}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Export-Batch-Id": str(batch_id),
            "X-Export-Count": str(len(records)),
        },
    )
