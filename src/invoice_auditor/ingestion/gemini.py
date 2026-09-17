"""Gemini structured-output extraction: document bytes -> InvoiceExtraction."""

import asyncio
import hashlib
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from ..config import Settings
from ..schemas.api import FileMetadata, ProcessInvoiceRequest
from ..schemas.extraction import InvoiceExtraction
from .key_pool import KeyPool

logger = logging.getLogger(__name__)

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


# The SDK retries these on the same key. A 429 is left to the key pool, which moves to another key
# instead of waiting out a quota that key has already used up.
SDK_RETRY_STATUS_CODES = (408, 500, 502, 503, 504)
# A key Google refuses outright (invalid, revoked, reported as leaked) is set aside this long.
REJECTED_KEY_REST_S = 15 * 60

REST_RATE_LIMIT = "rate limit"
REST_DAILY_QUOTA = "daily quota"
REST_REJECTED = "rejected"


class ExtractionError(RuntimeError):
    """Gemini failed, or answered with something that is not a usable extraction."""


class ExtractionThrottled(ExtractionError):
    """Every API key is resting after a rate limit, for longer than an upload may wait."""

    def __init__(self, *, retry_after_s: float, keys: int, daily: bool) -> None:
        self.retry_after_s = retry_after_s
        self.keys = keys
        self.daily = daily
        subject = "The Gemini API key is" if keys == 1 else f"All {keys} Gemini API keys are"
        if daily:
            message = f"{subject} out of today's free quota, so uploads are paused. Try again in {_duration(retry_after_s)}, once it resets."
        else:
            limit = "its rate limit" if keys == 1 else "their rate limits"
            message = f"{subject} at {limit} right now, so this batch is throttled. Try again in {_duration(retry_after_s)}."
        super().__init__(message)


@dataclass
class ExtractionProgress:
    """What a running extraction is doing, for callers that stream progress.

    `changed` is set whenever the other fields change; the caller clears it after reading them.
    """

    key_switches: int = 0
    waiting_until: float | None = None  # time.monotonic() when a resting key is usable again
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def snapshot(self, now: float) -> dict[str, int]:
        waiting_ms = 0 if self.waiting_until is None else max(0, math.ceil((self.waiting_until - now) * 1000))
        return {"key_switches": self.key_switches, "waiting_ms": waiting_ms}

    def _switched(self) -> None:
        self.key_switches += 1
        self.changed.set()

    def _waiting(self, until: float | None) -> None:
        self.waiting_until = until
        self.changed.set()


def _duration(seconds: float) -> str:
    seconds = max(1, math.ceil(seconds))
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = math.ceil(seconds / 60)
    if minutes < 90:
        return f"{minutes} minutes"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


# --- Reading Google's error details ------------------------------------------------------------


def _error_details(exc: genai_errors.APIError) -> list[dict[str, Any]]:
    body = exc.details if isinstance(exc.details, dict) else {}
    if isinstance(body.get("error"), dict):
        body = body["error"]
    entries = body.get("details")
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def is_rate_limited(exc: genai_errors.APIError) -> bool:
    return exc.code == 429 or exc.status == "RESOURCE_EXHAUSTED"


def is_key_rejected(exc: genai_errors.APIError) -> bool:
    """The key itself is refused (not the request), so another key may still work."""
    if exc.code in (401, 403) or exc.status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return True
    reasons = {str(entry.get("reason")) for entry in _error_details(exc)}
    return exc.code == 400 and ("API_KEY_INVALID" in reasons or "API key not valid" in (exc.message or ""))


def retry_delay_s(exc: genai_errors.APIError) -> float | None:
    """Google's RetryInfo hint, e.g. {"@type": ".../google.rpc.RetryInfo", "retryDelay": "37s"}."""
    for entry in _error_details(exc):
        if str(entry.get("@type", "")).endswith("google.rpc.RetryInfo"):
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", str(entry.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def is_daily_quota(exc: genai_errors.APIError) -> bool:
    """A QuotaFailure naming a per-day quota, e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier."""
    for entry in _error_details(exc):
        violations = entry.get("violations")
        if isinstance(violations, list) and any(isinstance(v, dict) and "PerDay" in str(v.get("quotaId", "")) for v in violations):
            return True
    return False


def seconds_until_daily_reset(now: datetime | None = None) -> float:
    """Gemini's per-day quotas reset at midnight Pacific time."""
    now = now or datetime.now(UTC)

    def pacific_offset(moment: datetime) -> timedelta:
        # US daylight saving: second Sunday of March 02:00 PST to first Sunday of November 02:00 PDT.
        march = datetime(moment.year, 3, 8, tzinfo=UTC)
        november = datetime(moment.year, 11, 1, tzinfo=UTC)
        starts = march + timedelta(days=(6 - march.weekday()) % 7, hours=10)
        ends = november + timedelta(days=(6 - november.weekday()) % 7, hours=9)
        return timedelta(hours=-7 if starts <= moment < ends else -8)

    local = now + pacific_offset(now)
    midnight = datetime(local.year, local.month, local.day, tzinfo=UTC) + timedelta(days=1)
    reset = midnight - pacific_offset(midnight - pacific_offset(now))
    return max(60.0, (reset - now).total_seconds() + 60)  # a minute's margin past the reset


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
    """Extracts invoices with Gemini, spreading requests over a pool of API keys.

    Requests take keys in turn. A key that answers RESOURCE_EXHAUSTED rests (for Google's
    RetryInfo delay, or until the daily quota resets) and the request moves straight on to the
    next key. Only when every key is resting does an upload wait, and only up to
    `max_throttle_wait_s`; past that it fails with ExtractionThrottled.
    """

    def __init__(
        self,
        *,
        model: str,
        api_keys: Sequence[str] = (),
        api_key: str | None = None,
        timeout_s: float = 120.0,
        max_attempts: int = 3,
        rate_limit_cooldown_s: float = 60.0,
        max_throttle_wait_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        keys = list(dict.fromkeys(key for key in (*api_keys, api_key) if key))
        if not keys:
            raise ValueError("At least one Gemini API key is required.")
        self._model = model
        self._pool = KeyPool([self._client_for(key, timeout_s, max_attempts) for key in keys], clock=clock)
        self._rate_limit_cooldown_s = rate_limit_cooldown_s
        self._max_throttle_wait_s = max_throttle_wait_s
        self._clock = clock
        self._sleep = sleep
        self._last_rejection: str | None = None

    @staticmethod
    def _client_for(api_key: str, timeout_s: float, max_attempts: int) -> genai.Client:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=max_attempts, http_status_codes=list(SDK_RETRY_STATUS_CODES)),
            ),
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def key_count(self) -> int:
        return len(self._pool)

    @property
    def pool(self) -> KeyPool:
        return self._pool

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiInvoiceExtractor":
        keys = settings.gemini_key_pool
        if not keys:
            raise RuntimeError("No Gemini API key is set: use GEMINI_API_KEY, GEMINI_API_KEYS or GEMINI_API_KEY_1, _2, ...")
        return cls(
            api_keys=keys,
            model=settings.gemini_model,
            timeout_s=settings.gemini_timeout_s,
            max_attempts=settings.gemini_max_attempts,
            rate_limit_cooldown_s=settings.gemini_rate_limit_cooldown_s,
            max_throttle_wait_s=settings.gemini_max_throttle_wait_s,
        )

    async def extract(self, document: bytes, mime_type: str, progress: ExtractionProgress | None = None) -> InvoiceExtraction:
        if mime_type not in SUPPORTED_MIME_TYPES:
            raise ValueError(f"Unsupported document type {mime_type!r}; expected PDF, JPEG or PNG.")
        if not document:
            raise ValueError("The document is empty.")
        if len(document) > MAX_INLINE_BYTES:
            raise ValueError(f"The document is larger than {MAX_INLINE_BYTES // (1024 * 1024)} MB.")

        progress = progress if progress is not None else ExtractionProgress()
        contents = [types.Part.from_bytes(data=document, mime_type=mime_type), USER_PROMPT]
        # Temperature is left at the default: Google advises against lowering it for Gemini 3
        # models, and the response schema already constrains the output.
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=InvoiceExtraction,
        )
        waited = 0.0
        # Each pass sends a request or waits for a key, and every failed request rests its key, so
        # the loop ends on its own; the bound only guards against a key that keeps failing after rests.
        for _ in range(4 * len(self._pool) + 4):
            key = self._pool.acquire()
            if key is None:
                delay = self._pool.next_available_in()
                reasons = self._pool.resting_reasons()
                if reasons == {REST_REJECTED}:
                    raise ExtractionError(f"Gemini rejected every configured API key. Last answer: {self._last_rejection}")
                if waited + delay > self._max_throttle_wait_s:
                    daily = REST_DAILY_QUOTA in reasons and REST_RATE_LIMIT not in reasons
                    raise ExtractionThrottled(retry_after_s=delay, keys=len(self._pool), daily=daily)
                logger.info("every Gemini API key is resting; waiting %.1fs for the next one", delay)
                progress._waiting(self._clock() + delay)
                await self._sleep(delay)
                waited += delay
                progress._waiting(None)
                continue

            try:
                response = await key.client.aio.models.generate_content(model=self._model, contents=contents, config=config)
            except genai_errors.APIError as exc:
                if is_rate_limited(exc):
                    daily = is_daily_quota(exc)
                    hint = retry_delay_s(exc)
                    if daily:
                        rest = max(hint or 0.0, seconds_until_daily_reset())
                    else:
                        rest = (hint + 1.0) if hint is not None else self._rate_limit_cooldown_s
                    self._pool.rest(key, rest, REST_DAILY_QUOTA if daily else REST_RATE_LIMIT)
                    logger.warning(
                        "Gemini %s hit its %s; resting it for %s (%d of %d keys available)",
                        key.label, "daily quota" if daily else "rate limit", _duration(rest), self._pool.available(), len(self._pool),
                    )
                    progress._switched()
                    continue
                if is_key_rejected(exc):
                    self._pool.rest(key, REJECTED_KEY_REST_S, REST_REJECTED)
                    self._last_rejection = f"{key.label}: {exc.code} {exc.status}: {exc.message}"
                    logger.error("Gemini refused %s (%s %s): %s; setting it aside", key.label, exc.code, exc.status, exc.message)
                    progress._switched()
                    continue
                raise ExtractionError(f"Gemini rejected the request ({exc.code} {exc.status}): {exc.message}") from exc
            return self._parse(response)
        raise ExtractionThrottled(retry_after_s=max(1.0, self._pool.next_available_in()), keys=len(self._pool), daily=False)

    @staticmethod
    def _parse(response: Any) -> InvoiceExtraction:
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
