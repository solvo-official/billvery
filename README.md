# Invoice & Expense Auditor

Multi-tenant backend that stores AI-extracted invoices, runs a rule engine over them, records
every finding in an immutable audit log, and routes suspicious invoices to human review.

```
document ──► Gemini structured output ──► POST /api/v1/invoices/process
             (GeminiInvoiceExtractor)        │
                                             ├─ per-tenant advisory lock
                                             ├─ vendor lookup / normalization
                                             ├─ 13 anomaly rules ──► anomaly_logs (append-only)
                                             └─ status: APPROVED | NEEDS_REVIEW
reviewer ──► POST /api/v1/anomalies/{id}/resolve ──► status re-derived: APPROVED | NEEDS_REVIEW | REJECTED
```

Stack: FastAPI · SQLAlchemy 2.0 (async, asyncpg) · Alembic · Pydantic v2 · PostgreSQL 15+ ·
google-genai.

The reviewer console (React dashboard on the live API) lives in [`frontend/`](frontend/README.md).
Both deploy together to Vercel with Neon as the database: see [Deploy](#deploy-vercel--neon).

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows; .venv/bin/python elsewhere
cp .env.example .env                                  # then set DATABASE_URL (and GEMINI_API_KEY)
invoice-auditor migrate
```

Create a tenant, a reviewer and an API key (the key is printed once; only its SHA-256 is stored):

```bash
invoice-auditor create-org --name "Gulf Traders" --country AE --currency AED
invoice-auditor create-user --org <organization_id> --email reviewer@example.com --role reviewer
invoice-auditor create-api-key --org <organization_id> --name "erp-integration"
invoice-auditor set-tax-limit --country AE --max-rate 5 --note "UAE VAT standard rate"
```

Run the API:

```bash
uvicorn invoice_auditor.main:create_app --factory --host 0.0.0.0 --port 8000
```

Interactive docs are at `/docs`; `/healthz` checks database connectivity.

Extract a document with Gemini and print the request body for `/process`:

```bash
invoice-auditor extract invoice.pdf --storage-url s3://bucket/invoice.pdf > body.json
```

In your own ingestion worker, use `GeminiInvoiceExtractor.extract(bytes, mime_type)` and
`build_process_request(...)` from `invoice_auditor.ingestion.gemini`.

## Deploy (Vercel + Neon)

One Vercel project serves both halves on one domain: the Vite build from the CDN, and the API as a
Python Function (`api/index.py`) that `vercel.json` routes `/api/*` and `/healthz` to. The browser
calls relative `/api/v1/...` paths, so there is no CORS and no API key in the page; people sign in
with Google and carry an HttpOnly session cookie.

```
repo root
├── api/index.py        ← Vercel Python Function: `app = create_app()`
├── src/invoice_auditor ← the FastAPI app (unchanged layout)
├── frontend/           ← Vite SPA, built to frontend/dist
├── requirements.txt    ← function dependencies (asyncpg; psycopg2 is not used)
├── .python-version     ← 3.14, the version the test suite runs on
└── vercel.json         ← build, function limits, rewrites (API, then SPA fallback)
```

1. **Neon.** Create a project in the region nearest your Vercel functions (Vercel's default is
   `iad1`, so pick AWS `us-east-1`). Copy both connection strings: the **pooled** one (host contains
   `-pooler`) and the **direct** one.
2. **Schema.** From your machine, run the migrations against the direct endpoint:
   `DATABASE_URL="<direct url>" invoice-auditor migrate`, then create the organization, users and
   (optionally) API keys with the CLI above, pointing at the same URL. Only registered email
   addresses can sign in.
3. **Google OAuth.** In Google Cloud Console > APIs & Services > Credentials, create an *OAuth client
   ID* of type *Web application*. Authorized redirect URI:
   `https://<your-project>.vercel.app/api/v1/auth/google/callback` (add one per custom domain).
4. **Vercel.** Push the repository to GitHub and import it (leave Root Directory as the repository
   root; `vercel.json` sets the Vite build). Add the environment variables below, then deploy.
5. **Check.** `https://<your-project>.vercel.app/healthz` should report `database: ok`,
   `auth.google: true` and `limits.max_upload_bytes: 4194304`.

| Variable | Required | Value |
| --- | --- | --- |
| `DATABASE_URL` | yes | Neon **pooled** URL, exactly as Neon shows it (`?sslmode=require` is handled) |
| `DATABASE_URL_UNPOOLED` | recommended | Neon direct URL (used by `invoice-auditor migrate`) |
| `JWT_SECRET` | yes | ≥ 32 random characters: `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `GOOGLE_CLIENT_ID` | yes | OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | yes | OAuth client secret |
| `GEMINI_API_KEY` | for uploads | Google AI Studio key; without it uploads answer 503 |
| `PUBLIC_URL` | custom domain | `https://audit.example.com`; defaults to the Vercel production domain |
| `GEMINI_MODEL` | no | defaults to `gemini-3.1-pro-preview` |

Vercel sets `VERCEL=1` itself, which switches the service to serverless defaults: no connection pool
(each request opens and closes one Neon connection through the pooler), uploaded originals stored
in the database (a function's file system is read-only and not durable), and a 4 MB upload limit
(Vercel refuses request bodies over 4.5 MB). `DB_POOL`, `DOCUMENT_STORAGE` and `MAX_UPLOAD_BYTES`
override each of these. Free-tier notes: originals count against Neon's storage quota, and the
console polls every 10 s while open, which keeps a Neon compute awake and uses function invocations.

## API

All endpoints except `/healthz` need `Authorization: Bearer <api key>`. Keys carry scopes:
`invoices:write`, `invoices:read`, `anomalies:resolve`. Errors always look like
`{"error": {"code": "...", "message": "...", "details": ...}}`. Money is returned as exact
decimal strings (`"1050.00"`, `"0.0125"`, `"1050.125"` for three-decimal currencies).

### `POST /api/v1/invoices/process`

```json
{
  "file": {
    "sha256": "<hex SHA-256 of the file bytes>",
    "filename": "inv-2026-0042.pdf",
    "content_type": "application/pdf",
    "size_bytes": 184223,
    "storage_url": "s3://invoices/inv-2026-0042.pdf"
  },
  "extraction": {
    "vendor_name": "Al Noor Trading L.L.C",
    "vendor_tax_id": "TRN 100200300400003",
    "vendor_confidence": 97,
    "invoice_number": "AN-2026-0042",
    "invoice_date": "2026-09-01",
    "due_date": "2026-10-01",
    "currency": "AED",
    "subtotal": 1000, "tax_amount": 50, "shipping_amount": 0, "discount_amount": 0,
    "total_amount": 1050,
    "line_items": [{"description": "Office chair", "quantity": 4, "unit_price": 250, "line_total": 1000}],
    "confidence": 94
  },
  "uploaded_by": "<optional user id in your organization>",
  "metadata": {"department": "Operations"}
}
```

Returns `201` with the audit report (same shape as the GET below). Optional
`Idempotency-Key` header: a retry with the same key and file returns `200` with the original
result; the same key with a different file returns `409`.

### `POST /api/v1/invoices/upload`

Multipart form: `file` (PDF, PNG or JPEG, up to 18 MB; the type is checked by its bytes),
optional `uploaded_by` and `metadata` (a JSON object), optional `Idempotency-Key` header.
Gemini extracts the document on the server (needs `GEMINI_API_KEY`, otherwise `503`), the
original is stored under `DOCUMENT_STORAGE_DIR`, and then the same processing as `/process`
runs. Problems found before extraction (type, size, unknown user) are ordinary JSON errors.
After that the response is an NDJSON stream, one event per line:

```
{"event":"received","filename":"inv.pdf","content_type":"application/pdf","size_bytes":84213,"sha256":"…"}
{"event":"extracting","model":"gemini-3.1-pro-preview","elapsed_ms":0,"reused":false}
{"event":"extracting","model":"gemini-3.1-pro-preview","elapsed_ms":5000}      ← heartbeat
{"event":"auditing"}
{"event":"complete","replayed":false,"audit":{ …same shape as GET /audit… }}
```

or `{"event":"error","error":{"code":"extraction_failed","message":"…"}}`. Uploading identical
bytes again reuses the earlier extraction (no second Gemini call) and is flagged
`DUPLICATE_FILE_HASH`.

### `GET /api/v1/invoices`

The feed, newest first: `status` (repeatable), `created_since`, `updated_since`, `q` (invoice
number, vendor, tax ID or file name), `limit` (≤ 500) and `cursor`. Each item has the full audit
shape. The response carries `next_cursor` and `server_time`; for a live view, poll with
`updated_since` a little before the previous `server_time`. Resolutions touch their invoice, so
polling sees them.

### `GET /api/v1/invoices/{id}/audit`

Status, vendor (as printed, and as on file), financial summary, line items, AI confidence,
document metadata and every anomaly, most severe first. Another tenant's invoice returns
`404`, same as a missing one.

### `GET /api/v1/invoices/{id}/document`

The original file, for invoices that came in through `/upload`.

### `GET /api/v1/organization` · `GET /api/v1/users`

The key's organization, and its active members (`can_resolve` marks who may resolve anomalies).

### `GET /healthz`

`{"status":"ok","version":"1.0.0","database":"ok","extraction":{"configured":true,"model":"…"}}`,
or `503` when the database is unreachable.

### `POST /api/v1/anomalies/{id}/resolve`

```json
{"resolution": "DISMISSED", "resolved_by": "<reviewer user id>", "note": "Checked against PO 7781"}
```

`DISMISSED` = false positive or accepted; `CONFIRMED` = the problem is real. The reviewer must be
an active `owner`, `admin` or `reviewer` of the organization. An anomaly can be resolved once
(`409` afterwards). The response includes the re-derived invoice status.

## Status machine

| Status | When |
|---|---|
| `PROCESSING` | Held by the upstream extraction step, before `/process` is called. |
| `NEEDS_REVIEW` | Any HIGH/CRITICAL anomaly is open. AI confidence < 90 is itself a HIGH (or, below 70, CRITICAL) anomaly. |
| `APPROVED` | No HIGH/CRITICAL anomaly is open or confirmed. |
| `REJECTED` | A reviewer CONFIRMED a HIGH/CRITICAL anomaly. |

## Anomaly rules

| Type | Severity | Trigger |
|---|---|---|
| `DUPLICATE_FILE_HASH` | CRITICAL | Same SHA-256 already submitted **in this organization** |
| `DUPLICATE_INVOICE_NUMBER` | HIGH | Same normalized number from the same vendor (or a look-alike vendor) within 90 days |
| `SIMILAR_INVOICE_NUMBER` | MEDIUM | Same vendor, date, currency and total; number ≥ 80% similar (e.g. `10022` vs `1O022`) |
| `VENDOR_TAX_ID_MISMATCH` | HIGH | Known vendor name, different tax ID than on file |
| `VENDOR_NAME_MISMATCH` | MEDIUM | Known tax ID, different vendor name |
| `SIMILAR_VENDOR_NAME` | MEDIUM | New vendor whose normalized name is ≥ 60% trigram-similar to an existing one |
| `MATH_TOTAL_MISMATCH` | LOW < 1% ≤ MEDIUM ≤ 5% < HIGH | `abs((subtotal + tax + shipping − discount) − total) > 0.05` |
| `PRICE_SPIKE` | HIGH | Unit price ≥ 2.5 × the organization's average for the same item (same currency, last 365 days, ≥ 3 purchases) |
| `TAX_RATE_EXCEEDED` | HIGH | `tax > subtotal × limit + 0.05`; limit from `organizations.settings.max_tax_rate_percent` or `tax_rate_limits` |
| `FUTURE_INVOICE_DATE` | HIGH | Dated after tomorrow (UTC) |
| `STALE_INVOICE_DATE` | MEDIUM | Dated more than 365 days ago |
| `MISSING_REQUIRED_FIELDS` | MEDIUM | vendor name, invoice number, invoice date or total missing |
| `LOW_EXTRACTION_CONFIDENCE` | HIGH (< 90) / CRITICAL (< 70) | Gemini's overall confidence |

All thresholds are environment variables (see `.env.example`). Vendor names are normalized
before matching: case, accents, punctuation and trailing legal forms are removed, so
`ABC Trading L.L.C.` and `ABC Trading LLC` are the same vendor. Look-alike vendors are
reported, never merged automatically.

## Data guarantees

- **Tenant isolation in the schema**: child rows reference parents through composite foreign
  keys on `(id, organization_id)`, so a line item, anomaly or vendor link cannot point at another
  tenant's row. Every query is also scoped by organization.
- **Immutable audit log**: triggers reject any change to a finding and any change after
  resolution, and reject deletes except when the whole organization is deleted. An invoice or
  vendor with dependants cannot be deleted (`RESTRICT`).
- **No races on duplicate checks**: `/process` takes a per-organization transaction-level
  advisory lock, so concurrent submissions of the same invoice are serialized.
- **Exact money**: `NUMERIC(18,4)` everywhere; amounts are parsed from JSON straight into
  `Decimal`.

## Decisions beyond the locked spec

1. **Gemini model.** Google shut down Gemini 1.5 Pro in September 2025, so calls to it fail. The
   default is `gemini-3.1-pro-preview`; set `GEMINI_MODEL` to change it. Temperature is left at
   the default, as Google recommends for Gemini 3.
2. **`REJECTED` status** for invoices with a CONFIRMED HIGH/CRITICAL anomaly; without it,
   resolving a real duplicate would have approved the invoice.
3. **Low AI confidence is an anomaly**, so a reviewer can clear it through the same resolve
   endpoint. That gives low-confidence invoices a path to approval.
4. **`users` and `api_keys` tables** from the original spec are kept: API keys authenticate
   tenants, and `resolved_by`/`uploaded_by` reference users.
5. **Tax limits ship empty.** Rates change, so none are hard-coded; set them with
   `set-tax-limit`. With no limit configured the tax rule is skipped. The base is the subtotal,
   as in the original spec.
6. **Price history** matches items by normalized description and excludes rejected invoices.
7. Dropped `extracted_total` and `audit_status`: there is one `status`, and the raw extraction
   is kept in `ai_extraction`.

## Tests

```bash
# unit tests only
pytest
# plus integration tests against a real server (creates and drops its own database)
TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/postgres pytest
```

This machine has a portable PostgreSQL 18 in `E:\claude-workspace\tools\pgsql`:

```powershell
E:\claude-workspace\tools\pgsql\bin\pg_ctl.exe -D E:\claude-workspace\tools\pgdata-test -o "-p 54329" -l E:\claude-workspace\tools\pgdata-test.log start
$env:TEST_DATABASE_URL = "postgresql+asyncpg://postgres@localhost:54329/postgres"
.venv\Scripts\python -m pytest
E:\claude-workspace\tools\pgsql\bin\pg_ctl.exe -D E:\claude-workspace\tools\pgdata-test stop
```

**Windows + Python 3.13+:** Python skips `.pth` files that have the Hidden attribute. If
`import invoice_auditor` fails after `pip install -e .`, check that
`.venv\Lib\site-packages\__editable__.invoice_auditor-1.0.0.pth` is not hidden.
