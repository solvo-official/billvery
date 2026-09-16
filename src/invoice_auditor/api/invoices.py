import asyncio
import base64
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path, PurePath
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..enums import ApiScope, InvoiceStatus
from ..errors import (
    AppError,
    Conflict,
    InvalidParameter,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    UnsupportedMediaType,
)
from ..ingestion.gemini import ExtractionError, GeminiInvoiceExtractor, sniff_content_type
from ..models import Invoice, User, Vendor
from ..schemas.api import (
    MAX_METADATA_BYTES,
    ApproveInvoiceRequest,
    ErrorResponse,
    FileMetadata,
    InvoiceAuditResponse,
    InvoicePage,
    ProcessInvoiceRequest,
)
from ..schemas.extraction import InvoiceExtraction
from ..services.invoice_processing import InvoiceProcessor
from ..services.review import AnomalyReviewService
from ..services.tenancy import get_active_member
from ..storage import DocumentStore
from .deps import Principal, SessionDep, SettingsDep, require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

HEARTBEAT_SECONDS = 5.0


# --- Serialization -----------------------------------------------------------------------------


def document_store(request: Request) -> DocumentStore:
    return request.app.state.documents


async def serialize(session: AsyncSession, invoices: Sequence[Invoice], store: DocumentStore) -> list[InvoiceAuditResponse]:
    """Audit responses for invoices whose anomalies and items are loaded; one query per lookup table."""
    vendor_ids = {i.vendor_id for i in invoices if i.vendor_id is not None}
    user_ids = {user for i in invoices for user in (i.uploaded_by, i.approved_by) if user is not None}
    vendors = {v.id: v for v in await session.scalars(select(Vendor).where(Vendor.id.in_(vendor_ids)))} if vendor_ids else {}
    users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_(user_ids)))} if user_ids else {}
    return [
        InvoiceAuditResponse.from_models(
            invoice,
            invoice.anomalies,
            items=invoice.items,
            vendor=vendors.get(invoice.vendor_id) if invoice.vendor_id else None,
            uploader=users.get(invoice.uploaded_by) if invoice.uploaded_by else None,
            approver=users.get(invoice.approved_by) if invoice.approved_by else None,
            document_available=store.owns(invoice.source_file_url),
        )
        for invoice in invoices
    ]


async def load_audit(session: AsyncSession, organization_id: UUID, invoice_id: UUID, store: DocumentStore) -> InvoiceAuditResponse:
    invoice = await session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.organization_id == organization_id)
        .options(selectinload(Invoice.anomalies), selectinload(Invoice.items))
        .execution_options(populate_existing=True)
    )
    if invoice is None:  # also the answer for another tenant's invoice: existence is not leaked
        raise NotFound("Invoice not found.")
    [audit] = await serialize(session, [invoice], store)
    return audit


# --- List ------------------------------------------------------------------------------------------


def _encode_cursor(invoice: Invoice) -> str:
    raw = f"{invoice.created_at.isoformat()}|{invoice.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created_at, _, invoice_id = raw.partition("|")
        return datetime.fromisoformat(created_at), UUID(invoice_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidParameter("The cursor is not valid. Start again without one.") from exc


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("", response_model=InvoicePage, responses={422: {"model": ErrorResponse}})
async def list_invoices(
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))],
    session: SessionDep,
    request: Request,
    status_filter: Annotated[list[InvoiceStatus] | None, Query(alias="status", description="Repeat to match any of several.")] = None,
    created_since: Annotated[datetime | None, Query(description="Only invoices received at or after this time.")] = None,
    updated_since: Annotated[datetime | None, Query(description="Only invoices created or changed at or after this time.")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, description="Invoice number, vendor, tax ID or file name.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: str | None = None,
) -> InvoicePage:
    """Invoices with their audit results, newest first, one page at a time.

    For a live view, poll with `updated_since` a little before the previous page's
    `server_time`: every invoice created or changed since then comes back, including status
    changes from reviews.
    """
    server_time = await session.scalar(select(func.now()))
    stmt = (
        select(Invoice)
        .where(Invoice.organization_id == principal.organization_id)
        .options(selectinload(Invoice.anomalies), selectinload(Invoice.items))
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(limit + 1)
    )
    if status_filter:
        stmt = stmt.where(Invoice.status.in_([s.value for s in status_filter]))
    if created_since is not None:
        stmt = stmt.where(Invoice.created_at >= created_since)
    if updated_since is not None:
        stmt = stmt.where(Invoice.updated_at >= updated_since)
    if q:
        pattern = _like(q.strip())
        stmt = stmt.where(
            or_(
                Invoice.invoice_number.ilike(pattern, escape="\\"),
                Invoice.vendor_name.ilike(pattern, escape="\\"),
                Invoice.vendor_tax_id.ilike(pattern, escape="\\"),
                Invoice.source_file_name.ilike(pattern, escape="\\"),
            )
        )
    if cursor:
        at, last_id = _decode_cursor(cursor)
        stmt = stmt.where(or_(Invoice.created_at < at, and_(Invoice.created_at == at, Invoice.id < last_id)))

    invoices = list((await session.scalars(stmt)).unique())
    has_more = len(invoices) > limit
    invoices = invoices[:limit]
    return InvoicePage(
        items=await serialize(session, invoices, document_store(request)),
        next_cursor=_encode_cursor(invoices[-1]) if has_more and invoices else None,
        server_time=server_time,
    )


# --- Process (pre-extracted JSON) ----------------------------------------------------------------


@router.post(
    "/process",
    status_code=status.HTTP_201_CREATED,
    response_model=InvoiceAuditResponse,
    responses={
        200: {"model": InvoiceAuditResponse, "description": "Replay of an earlier request with the same Idempotency-Key."},
        409: {"model": ErrorResponse, "description": "Idempotency-Key reused for a different file."},
        422: {"model": ErrorResponse, "description": "Invalid payload or unknown uploaded_by user."},
    },
)
async def process_invoice(
    payload: ProcessInvoiceRequest,
    response: Response,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_WRITE))],
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=255)] = None,
) -> InvoiceAuditResponse:
    """Store an invoice that was already extracted (e.g. by your own Gemini worker), run every
    anomaly rule and set its status.

    The invoice becomes NEEDS_REVIEW when any HIGH/CRITICAL anomaly is found (low AI
    confidence counts as one), otherwise APPROVED.
    """
    if payload.uploaded_by is not None:
        principal.acting_as(payload.uploaded_by)
    outcome = await InvoiceProcessor(session, settings).process(
        principal.organization_id, payload, idempotency_key=idempotency_key
    )
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    return await load_audit(session, principal.organization_id, outcome.invoice_id, document_store(request))


# --- Upload (file in, Gemini on the server) ------------------------------------------------------


def get_extractor(request: Request) -> GeminiInvoiceExtractor:
    extractor = getattr(request.app.state, "extractor", None)
    if extractor is None:
        raise ServiceUnavailable("Invoice extraction isn't configured on this server. Set GEMINI_API_KEY and restart it.")
    return extractor


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge(f"The file is larger than {limit // (1024 * 1024)} MB. Export a smaller PDF.")
        chunks.append(chunk)
    return b"".join(chunks)


def _event(name: str, **fields: Any) -> bytes:
    return (json.dumps({"event": name, **fields}, default=str, separators=(",", ":")) + "\n").encode()


@router.post(
    "/upload",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "An NDJSON stream of progress events, one JSON object per line: `received`, "
                "`extracting` (repeated as a heartbeat with `elapsed_ms`), `auditing`, then "
                "`complete` with the full `audit`, or `error` with an `error` object."
            ),
            "content": {"application/x-ndjson": {}},
        },
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse, "description": "Gemini extraction is not configured."},
    },
)
async def upload_invoice(
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_WRITE))],
    settings: SettingsDep,
    extractor: Annotated[GeminiInvoiceExtractor, Depends(get_extractor)],
    file: Annotated[UploadFile, File(description="PDF, PNG or JPEG; the ceiling is in GET /healthz.")],
    uploaded_by: Annotated[UUID | None, Form(description="User in your organization who submitted the document.")] = None,
    metadata: Annotated[str | None, Form(description="JSON object of free-form tags, e.g. {\"department\": \"Ops\"}.")] = None,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=255)] = None,
) -> StreamingResponse:
    """Upload a document: Gemini extracts it on the server, then every audit rule runs.

    Problems found before extraction starts (file type, size, unknown user) are ordinary JSON
    errors. After that the response is a stream of progress events ending in `complete` or
    `error`. Re-uploading identical bytes reuses the earlier extraction instead of calling
    Gemini again, and the duplicate-file rule flags it.
    """
    organization_id = principal.organization_id
    if uploaded_by is None:
        uploaded_by = principal.user_id  # a signed-in person uploads as themselves
    elif uploaded_by is not None:
        principal.acting_as(uploaded_by)
    content = await _read_limited(file, settings.max_upload_bytes)
    if not content:
        raise InvalidParameter("The file is empty.")
    content_type = sniff_content_type(content)
    if content_type is None:
        raise UnsupportedMediaType("Only PDF, PNG and JPEG files can be audited.")
    try:
        tags = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise InvalidParameter("metadata must be a JSON object.") from exc
    if not isinstance(tags, dict) or len(metadata or "") > MAX_METADATA_BYTES:
        raise InvalidParameter(f"metadata must be a JSON object of at most {MAX_METADATA_BYTES} bytes.")

    sha256 = hashlib.sha256(content).hexdigest()
    filename = (PurePath(file.filename or "").name or "upload")[:255]
    db = request.app.state.db
    store = document_store(request)

    # Checks that need the database happen before the stream opens, so they are plain errors.
    async with db.session() as session:
        if uploaded_by is not None:
            await get_active_member(session, organization_id, uploaded_by)
        replay: Invoice | None = None
        if idempotency_key is not None:
            replay = await session.scalar(
                select(Invoice).where(Invoice.organization_id == organization_id, Invoice.idempotency_key == idempotency_key)
            )
            if replay is not None and replay.file_hash != sha256:
                raise Conflict("This Idempotency-Key was already used for a different file.", details={"invoice_id": str(replay.id)})
        earlier = await session.scalar(
            select(Invoice.ai_extraction)
            .where(Invoice.organization_id == organization_id, Invoice.file_hash == sha256)
            .order_by(Invoice.created_at)
            .limit(1)
        )
        replay_id = replay.id if replay is not None else None

    async def events() -> AsyncIterator[bytes]:
        yield _event("received", filename=filename, content_type=content_type, size_bytes=len(content), sha256=sha256)
        extraction_task: asyncio.Task[InvoiceExtraction] | None = None
        try:
            if replay_id is not None:
                async with db.session() as session:
                    audit = await load_audit(session, organization_id, replay_id, store)
                yield _event("complete", replayed=True, audit=audit.model_dump(mode="json"))
                return

            storage_url = await store.save(db, organization_id, sha256, content)
            reused = None
            if earlier:
                try:
                    reused = InvoiceExtraction.model_validate(earlier)
                except ValidationError:
                    reused = None
            if reused is not None:
                extraction = reused
                yield _event("extracting", model=extractor.model, elapsed_ms=0, reused=True)
            else:
                started = time.monotonic()
                yield _event("extracting", model=extractor.model, elapsed_ms=0, reused=False)
                extraction_task = asyncio.create_task(extractor.extract(content, content_type))
                while True:
                    try:
                        extraction = await asyncio.wait_for(asyncio.shield(extraction_task), HEARTBEAT_SECONDS)
                        break
                    except TimeoutError:
                        yield _event("extracting", model=extractor.model, elapsed_ms=round((time.monotonic() - started) * 1000))

            yield _event("auditing")
            payload = ProcessInvoiceRequest(
                file=FileMetadata(sha256=sha256, filename=filename, content_type=content_type, size_bytes=len(content), storage_url=storage_url),
                extraction=extraction,
                uploaded_by=uploaded_by,
                metadata=tags,
            )
            async with db.session() as session:
                outcome = await InvoiceProcessor(session, settings).process(organization_id, payload, idempotency_key=idempotency_key)
                audit = await load_audit(session, organization_id, outcome.invoice_id, store)
            logger.info("upload %s (%s) audited as invoice %s: %s", filename, sha256[:12], audit.invoice_id, audit.status)
            yield _event("complete", replayed=outcome.replayed, audit=audit.model_dump(mode="json"))
        except ExtractionError as exc:
            logger.warning("extraction failed for %s (%s): %s", filename, sha256[:12], exc)
            yield _event("error", error={"code": "extraction_failed", "message": str(exc)})
        except AppError as exc:
            yield _event("error", error={"code": exc.code, "message": exc.message, "details": exc.details})
        except Exception:
            logger.exception("upload of %s (%s) failed", filename, sha256[:12])
            yield _event("error", error={"code": "internal_error", "message": "The audit failed unexpectedly. Try again."})
        finally:
            if extraction_task is not None and not extraction_task.done():
                extraction_task.cancel()  # the client went away mid-extraction

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --- Read one --------------------------------------------------------------------------------------


@router.get("/{invoice_id}/audit", response_model=InvoiceAuditResponse, responses={404: {"model": ErrorResponse}})
async def get_invoice_audit(
    invoice_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))],
    session: SessionDep,
) -> InvoiceAuditResponse:
    """Audit results for one invoice: status, amounts, line items and every anomaly, most severe first."""
    return await load_audit(session, principal.organization_id, invoice_id, document_store(request))


@router.post(
    "/{invoice_id}/approve",
    response_model=InvoiceAuditResponse,
    responses={
        403: {"model": ErrorResponse, "description": "The reviewer's role may not approve invoices."},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse, "description": "The invoice is not awaiting review (already approved, rejected or processing)."},
        422: {"model": ErrorResponse, "description": "approved_by is not an active user of this organization."},
    },
)
async def approve_invoice(
    invoice_id: UUID,
    payload: ApproveInvoiceRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.ANOMALIES_RESOLVE))],
    session: SessionDep,
) -> InvoiceAuditResponse:
    """Approve an invoice that is awaiting review, and archive it as approved.

    Every open finding is dismissed with the reviewer's note, in one transaction, and the invoice
    records who approved it and when. Resolutions are final: an approved invoice can't be reopened.
    """
    principal.acting_as(payload.approved_by)
    await AnomalyReviewService(session).approve(principal.organization_id, invoice_id, payload)
    return await load_audit(session, principal.organization_id, invoice_id, document_store(request))


@router.get(
    "/{invoice_id}/document",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "image/png": {}, "image/jpeg": {}}}, 404: {"model": ErrorResponse}},
)
async def get_invoice_document(
    invoice_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scope(ApiScope.INVOICES_READ))],
    session: SessionDep,
) -> Response:
    """The original uploaded file, when this service stored it (uploads through /upload)."""
    invoice = await session.scalar(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.organization_id == principal.organization_id)
    )
    if invoice is None:
        raise NotFound("Invoice not found.")
    stored = await document_store(request).fetch(request.app.state.db, invoice.source_file_url, principal.organization_id)
    if stored is None:
        raise NotFound("The original file for this invoice isn't stored by this service.")
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": f'inline; filename="{PurePath(invoice.source_file_name or "document").name}"',
    }
    if isinstance(stored, Path):
        return FileResponse(
            stored,
            media_type=invoice.file_mime_type,
            filename=invoice.source_file_name,
            content_disposition_type="inline",
            headers={"Cache-Control": "private, max-age=3600"},
        )
    return Response(content=stored, media_type=invoice.file_mime_type, headers=headers)
