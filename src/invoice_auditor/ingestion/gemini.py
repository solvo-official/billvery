"""Gemini structured-output extraction: document bytes -> InvoiceExtraction."""

import hashlib
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from ..config import Settings
from ..schemas.api import FileMetadata, ProcessInvoiceRequest
from ..schemas.extraction import InvoiceExtraction

SUPPORTED_MIME_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
# Inline request payloads above ~20 MB are rejected by the Gemini API.
MAX_INLINE_BYTES = 18 * 1024 * 1024

SYSTEM_INSTRUCTION = """\
You extract accounts-payable data from invoice documents for an audit system. Accuracy matters
more than completeness: a wrong value is worse than a missing one.

- Report only what the document shows. Never guess, and never calculate a value that is not
  printed (for example a missing subtotal): use null instead.
- The vendor is the business that issued the invoice (the seller), not the customer billed.
- vendor_tax_id: the seller's tax registration number only (VAT, GST, TRN, EIN, ABN, ...),
  without labels such as "TRN:" or "VAT No.".
- Dates are YYYY-MM-DD. Resolve day/month order from the vendor's country and other dates shown.
- Amounts are plain numbers: no currency symbols or thousands separators, "." as the decimal
  point. Keep every printed decimal; some currencies use three.
- discount_amount is positive even when the document prints it as negative.
- currency is an ISO 4217 code (USD, EUR, AED, INR, KWD, ...), inferred from symbols and the
  vendor's country when not spelled out.
- line_items: one entry per priced line. Do not include subtotal, tax, shipping or total rows.
- confidence and vendor_confidence are percentages from 0 to 100, not fractions of 1. Lower them
  when the document is blurry, handwritten, cropped, or values are hard to read.
"""

USER_PROMPT = "Extract the invoice data from this document."


class ExtractionError(RuntimeError):
    """Gemini failed, or answered with something that is not a usable extraction."""


def sniff_content_type(document: bytes) -> str | None:
    """Identify a supported document by its magic bytes; the declared type is not trusted."""
    if document.startswith(b"%PDF-"):
        return "application/pdf"
    if document.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if document.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


class GeminiInvoiceExtractor:
    def __init__(self, *, api_key: str, model: str, timeout_s: float = 120.0, max_attempts: int = 3) -> None:
        self._model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=max_attempts),  # retries 408/429/5xx with backoff
            ),
        )

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiInvoiceExtractor":
        if settings.gemini_api_key is None:
            raise RuntimeError("GEMINI_API_KEY is not set")
        return cls(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            timeout_s=settings.gemini_timeout_s,
            max_attempts=settings.gemini_max_attempts,
        )

    async def extract(self, document: bytes, mime_type: str) -> InvoiceExtraction:
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError(f"Unsupported document type {mime_type!r}; expected PDF, JPEG or PNG.")
        if not document:
            raise ValueError("The document is empty.")
        if len(document) > MAX_INLINE_BYTES:
            raise ValueError(f"The document is larger than {MAX_INLINE_BYTES // (1024 * 1024)} MB.")

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[types.Part.from_bytes(data=document, mime_type=mime_type), USER_PROMPT],
                # Temperature is left at the default: Google advises against lowering it for
                # Gemini 3 models, and the response schema already constrains the output.
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=InvoiceExtraction,
                ),
            )
        except genai_errors.APIError as exc:
            raise ExtractionError(f"Gemini rejected the request ({exc.code} {exc.status}): {exc.message}") from exc
        text = response.text
        if not text:
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            raise ExtractionError(f"Gemini returned no content (finish_reason={finish_reason}).")
        try:
            # Parse the raw JSON text ourselves: amounts go straight to Decimal, never via float.
            return InvoiceExtraction.model_validate_json(text)
        except ValidationError as exc:
            raise ExtractionError(f"Gemini output does not match the extraction schema: {exc}") from exc


def build_process_request(
    document: bytes,
    *,
    filename: str,
    mime_type: str,
    extraction: InvoiceExtraction,
    storage_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProcessInvoiceRequest:
    """Assemble the body for POST /api/v1/invoices/process."""
    return ProcessInvoiceRequest(
        file=FileMetadata(
            sha256=hashlib.sha256(document).hexdigest(),
            filename=filename,
            content_type=mime_type,
            size_bytes=len(document),
            storage_url=storage_url,
        ),
        extraction=extraction,
        metadata=metadata or {},
    )
