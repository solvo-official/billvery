"""ASGI application factory. Run with:

    uvicorn invoice_auditor.main:create_app --factory --host 0.0.0.0 --port 8000
"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import anomalies, audit_trail, auth, exports, health, invoices, organization, vendors
from .config import Settings, get_settings
from .db import Database
from .errors import AppError
from .ingestion.gemini import GeminiInvoiceExtractor
from .schemas.api import ErrorResponse
from .storage import DatabaseDocumentStore, DocumentStore, LocalDocumentStore

logger = logging.getLogger("invoice_auditor")
access_logger = logging.getLogger("invoice_auditor.access")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _error_response(
    status_code: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": jsonable_encoder(details)}}
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return _error_response(exc.status_code, exc.code, exc.message, exc.details, headers)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "validation_error", "The request is invalid.", exc.errors())


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, "http_error", str(exc.detail), headers=exc.headers)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return _error_response(500, "internal_error", "An unexpected error occurred.")


async def _request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    # Refuse oversized uploads before the multipart parser spools them to disk.
    if request.url.path.endswith("/invoices/upload"):
        limit = request.app.state.settings.max_upload_bytes + 64 * 1024  # form overhead
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return _error_response(
                413, "payload_too_large", f"The file is larger than {request.app.state.settings.max_upload_bytes // (1024 * 1024)} MB."
            )
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    access_logger.info(
        "%s %s -> %s in %.1f ms (request_id=%s)",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        request_id,
    )
    return response


def _document_store(settings: Settings) -> DocumentStore:
    """Where uploaded originals go: the database when the file system isn't durable."""
    if settings.documents_in_database:
        return DatabaseDocumentStore()
    return LocalDocumentStore(settings.document_storage_dir)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await app.state.db.dispose()

    app = FastAPI(
        title="Billvery API",
        version=__version__,
        lifespan=lifespan,
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    # Built here rather than in the lifespan: creating the engine opens no connection, and a
    # serverless host may load the module and serve a request without running lifespan events.
    app.state.db = Database(settings)
    app.state.documents = _document_store(settings)
    # Uploads need Gemini; without a key the rest of the API still works.
    app.state.extractor = GeminiInvoiceExtractor.from_settings(settings) if settings.gemini_key_pool else None
    if app.state.extractor is None:
        logger.warning("No Gemini API key is set: POST /api/v1/invoices/upload will answer 503")
    elif app.state.extractor.key_count > 1:
        logger.info("Gemini extraction rotates through %d API keys", app.state.extractor.key_count)
    if settings.jwt_secret is None:
        logger.warning("JWT_SECRET is not set: browser sign-in is disabled, only API keys can authenticate")
    app.middleware("http")(_request_context)
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(organization.router)
    app.include_router(exports.router)  # before invoices: "/export" is a fixed path, not an invoice id
    app.include_router(invoices.router)
    app.include_router(audit_trail.router)
    app.include_router(anomalies.router)
    app.include_router(vendors.router)
    return app
