# Invoice Auditor Console

Reviewer console for the invoice auditor: headline metrics, document ingestion, a live audit
feed, and the inspection drawer where a reviewer checks extracted values against the document
and resolves each finding. Everything shown comes from the audit API.

Vite 8 · React 19 · TypeScript 7 · Tailwind CSS 4 · Radix primitives (shadcn-style) ·
lucide-react · sonner. Fonts (IBM Plex Sans, Sans Condensed, Mono) are bundled.

## Run it

```bash
cp .env.example .env.local   # set AUDITOR_API_URL and AUDITOR_API_KEY
npm install
npm run dev                  # http://localhost:5173
```

The browser only talks to its own origin. The dev server (and `npm run preview`) proxies
`/api` and `/healthz` to `AUDITOR_API_URL` and adds `Authorization: Bearer $AUDITOR_API_KEY`
on the way, so the API key never reaches browser code. To deploy the static build
(`npm run build` → `dist/`), put the same rule in your reverse proxy, e.g. nginx:

```nginx
location /api/    { proxy_pass http://audit-api:8000; proxy_set_header Authorization "Bearer <key>"; proxy_buffering off; }
location /healthz { proxy_pass http://audit-api:8000; }
```

`proxy_buffering off` keeps the upload progress stream live.

## How it uses the API

| Console | Endpoint |
|---|---|
| Initial load | `GET /api/v1/organization`, `GET /api/v1/users`, `GET /api/v1/invoices?created_since=<90 days>` plus every `status=NEEDS_REVIEW` invoice, paged by cursor |
| Live feed (every 10 s while **Live**) | `GET /api/v1/invoices?updated_since=<last server_time − 30 s>` |
| Upload | `POST /api/v1/invoices/upload` (multipart); the NDJSON stream drives Upload → Extract → Verify → Complete |
| Drawer | `GET /api/v1/invoices/{id}/audit` on open; `GET /api/v1/invoices/{id}/document` for the original file |
| Resolve & Commit Audit | `POST /api/v1/anomalies/{id}/resolve` per decided finding, then the audit is re-read |
| Engine status | `GET /healthz` every 30 s |

The four metric cards are computed in the browser from the loaded ledger (last 90 days), in
the organization's currency; invoices in other currencies are converted at fixed reference
rates. The risk score is a client-side heuristic over each invoice's findings.

**Acting as:** the API key identifies the organization, not a person. Pick who you are from
the top-right menu (anyone with the owner, admin or reviewer role). Uploads are recorded with
that user as `uploaded_by` and resolutions with them as `resolved_by`. The choice is
remembered in this browser. A production deployment would put the console behind single
sign-on and derive this from the session.

## Structure

```
src/
  App.tsx                         connection gate + page layout
  lib/
    api.ts                        typed client, NDJSON upload reader, errors
    types.ts                      API contract types (snake_case, decimal strings for money)
    money.ts                      exact decimal math on bigint units; currency-aware formatting
    rules.ts                      ports of the backend's status derivation and footing rule,
                                  risk score, anomaly catalog
  state/
    audit-store.tsx               connection, ledger, polling, reviewer selection
    metrics.ts                    the four headline metrics and their trends
  components/
    shell/TopBar.tsx              organization, engine health, acting-as menu
    stats/                        StatsRibbon, Sparkline
    ingestion/IngestionDropzone.tsx
    feed/                         InvoiceTable, ReviewQueue
    inspection/                   AnomalyInspectionDrawer, DocumentViewer, ExtractedFields,
                                  AnomalyCard, ResolutionPanel
    ui/                           Button, Tooltip, DropdownMenu, Sheet (Radix)
```

## Keyboard

`/` search the feed · `J` / `K` next or previous invoice in the review queue ·
`Ctrl/⌘ + Enter` commit from the resolution note · `Esc` close the drawer.
