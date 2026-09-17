/**
 * Client for the audit API.
 *
 * Requests are same-origin by default (`/api/v1/...`, `/healthz`), which is what production on
 * Vercel needs: the SPA and the Python function share one domain, so there is no CORS and the
 * browser sends its session cookie automatically. In local development the Vite dev server
 * proxies those same paths to http://127.0.0.1:8000 and adds the API key server-side, so the key
 * never reaches browser code. Set VITE_API_BASE_URL only to point the console at a different
 * origin (a staging API); it is left unset on Vercel.
 *
 * Every call either resolves with a value that passed the shape checks below or rejects with an
 * ApiError. Nothing else escapes: network failures, timeouts, 5xx pages from the proxy and
 * malformed JSON all become ApiErrors the UI can show, so a bad response can't crash a render.
 */
import {
  INVOICE_STATUSES,
  type ApproveInvoiceRequest,
  type InviteUserRequest,
  type Session,
  type UpdateOrganizationRequest,
  type UpdateUserRequest,
  type Workspace,
  type Health,
  type InvoicePage,
  type InvoiceRecord,
  type InvoiceStatus,
  type Organization,
  type ResolveAnomalyRequest,
  type ResolveAnomalyResponse,
  type UploadEvent,
  type User,
} from "./types";

export const BASE = ((import.meta.env.VITE_API_BASE_URL as string | undefined) || "").replace(/\/+$/, "");

export const ACCEPTED_TYPES = { "application/pdf": "PDF", "image/png": "PNG", "image/jpeg": "JPEG" } as const;
/** Matches the server's limit (Gemini accepts ~20 MB inline). */
export const MAX_UPLOAD_BYTES = 18 * 1024 * 1024;
/** JSON calls give up after this long; uploads stream for as long as extraction takes. */
const REQUEST_TIMEOUT_MS = 30_000;
/** Invoices per request. Kept well under a serverless platform's response size ceiling. */
const PAGE_SIZE = 200;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The API could not be reached at all (server down, proxy target unavailable, timeout). */
  get unreachable(): boolean {
    return this.status === 0;
  }

  /** The request was cancelled by the caller; nothing to report. */
  get aborted(): boolean {
    return this.code === "aborted";
  }
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof DOMException && error.name === "AbortError") return new ApiError(0, "aborted", "The request was cancelled.");
  if (error instanceof DOMException && error.name === "TimeoutError") return new ApiError(0, "timeout", "The audit API took too long to answer.");
  return new ApiError(0, "unexpected", error instanceof Error ? error.message : "Something went wrong.");
}

/** The caller's signal, if any, combined with a timeout. */
function withTimeout(signal: AbortSignal | null | undefined, ms: number): AbortSignal | undefined {
  if (typeof AbortSignal.timeout !== "function") return signal ?? undefined;
  const timeout = AbortSignal.timeout(ms);
  if (!signal) return timeout;
  return typeof AbortSignal.any === "function" ? AbortSignal.any([signal, timeout]) : signal;
}

async function send(path: string, init: RequestInit = {}, timeoutMs: number | null = REQUEST_TIMEOUT_MS): Promise<Response> {
  const signal = timeoutMs === null ? init.signal : withTimeout(init.signal, timeoutMs);
  try {
    return await fetch(`${BASE}${path}`, { ...init, signal, headers: { Accept: "application/json", ...init.headers } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") throw toApiError(error);
    if (error instanceof DOMException && error.name === "AbortError") {
      // AbortSignal.any reports a timeout as an abort whose reason is the TimeoutError.
      if (signal?.reason instanceof DOMException && signal.reason.name === "TimeoutError") throw toApiError(signal.reason);
      throw error;
    }
    throw new ApiError(0, "network_error", "Can't reach the audit API. Check that the backend is running.");
  }
}

async function readError(response: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown } } | null = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const error = body?.error;
  if (error?.message) return new ApiError(response.status, error.code ?? `http_${response.status}`, error.message, error.details);
  // No error envelope: the backend answers every error (its own 500s included) with one, so a
  // bare 5xx came from the dev proxy or a gateway because the backend itself did not answer.
  if (response.status >= 500) return new ApiError(0, "unreachable", "The audit API isn't responding. Check that the backend is running.");
  return new ApiError(response.status, `http_${response.status}`, response.statusText || `Request failed (${response.status}).`);
}

async function request<T>(path: string, init: RequestInit | undefined, parse: (value: unknown) => T): Promise<T> {
  const response = await send(path, init);
  if (!response.ok) throw await readError(response);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(response.status, "bad_response", "The audit API sent a response the console couldn't read.");
  }
  return parse(body);
}

// --- Response shape checks -------------------------------------------------------------------
// Light, structural: enough that every field the UI dereferences is present with the right kind.

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

function malformed(what: string): never {
  throw new ApiError(0, "bad_response", `The audit API sent a malformed ${what}.`);
}

function invoice(value: unknown): InvoiceRecord {
  if (
    !isObject(value) ||
    typeof value.invoice_id !== "string" ||
    !INVOICE_STATUSES.includes(value.status as InvoiceStatus) ||
    !isObject(value.vendor) ||
    !isObject(value.financial_summary) ||
    !isObject(value.document) ||
    !Array.isArray(value.anomalies) ||
    !Array.isArray(value.line_items) ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    malformed("invoice");
  }
  // Servers before migration 0003 don't send `approval`.
  return { ...(value as unknown as InvoiceRecord), approval: isObject(value.approval) ? (value.approval as unknown as InvoiceRecord["approval"]) : null };
}

function invoicePage(value: unknown): InvoicePage {
  if (!isObject(value) || !Array.isArray(value.items) || typeof value.server_time !== "string") malformed("invoice page");
  return { items: value.items.map(invoice), next_cursor: typeof value.next_cursor === "string" ? value.next_cursor : null, server_time: value.server_time };
}

function session(value: unknown): Session {
  if (!isObject(value) || !isObject(value.organization)) malformed("session");
  return value as unknown as Session;
}

function organization(value: unknown): Organization {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.name !== "string" || typeof value.currency_code !== "string") malformed("organization");
  return value as unknown as Organization;
}

function users(value: unknown): User[] {
  if (!Array.isArray(value) || !value.every((user) => isObject(user) && typeof user.id === "string" && typeof user.name === "string")) malformed("user list");
  return value as User[];
}

function workspaceList(value: unknown): Workspace[] {
  if (!Array.isArray(value) || !value.every((item) => isObject(item) && isObject(item.organization))) malformed("workspace list");
  return value as Workspace[];
}

function member(value: unknown): User {
  if (!isObject(value) || typeof value.id !== "string" || typeof value.email !== "string") malformed("team member");
  return value as unknown as User;
}

function health(value: unknown): Health {
  if (!isObject(value) || !isObject(value.extraction)) malformed("health check");
  return value as unknown as Health;
}

function resolution(value: unknown): ResolveAnomalyResponse {
  if (!isObject(value) || !isObject(value.anomaly) || typeof value.invoice_id !== "string") malformed("resolution");
  return value as unknown as ResolveAnomalyResponse;
}

// --- Calls -------------------------------------------------------------------------------------

export interface ListParams {
  status?: InvoiceStatus[];
  created_since?: string;
  updated_since?: string;
  q?: string;
  limit?: number;
  cursor?: string;
}

function query(params: ListParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    for (const item of Array.isArray(value) ? value : [value]) search.append(key, String(item));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface UploadOptions {
  uploadedBy?: string;
  signal?: AbortSignal;
  onEvent?: (event: UploadEvent) => void;
}

export const api = {
  async health(signal?: AbortSignal): Promise<{ health: Health; latencyMs: number }> {
    const started = performance.now();
    const result = await request("/healthz", { signal, cache: "no-store" }, health);
    return { health: result, latencyMs: Math.round(performance.now() - started) };
  },

  /** Who the caller is. Rejects with a 401 ApiError when nobody is signed in. */
  session: (signal?: AbortSignal) => request("/api/v1/auth/session", { signal, cache: "no-store" }, session),

  /** Where the browser goes to sign in with Google; `next` returns it to the same view. */
  signInUrl: (next: string = window.location.hash || "/") =>
    `${BASE}/api/v1/auth/google/login?next=${encodeURIComponent(next.startsWith("#") ? `/${next}` : next)}`,

  /** Ends the browser session. The 204 carries no body, so the response is not parsed. */
  async logout(): Promise<void> {
    await send("/api/v1/auth/logout", { method: "POST" });
  },

  organization: () => request("/api/v1/organization", undefined, organization),

  /** Rename the workspace or change its country and currency (owners and admins). */
  updateOrganization: (body: UpdateOrganizationRequest) =>
    request("/api/v1/organization", { ...json(body), method: "PATCH" }, organization),

  /** The workspaces this Google account belongs to, most recently used first. */
  workspaces: (signal?: AbortSignal) => request("/api/v1/auth/workspaces", { signal, cache: "no-store" }, workspaceList),

  /** Continue in another workspace; the server sets a new session cookie. */
  switchWorkspace: (organizationId: string) =>
    request("/api/v1/auth/workspaces/switch", json({ organization_id: organizationId }), session),

  /** Create another workspace owned by the signed-in account, and switch to it. */
  createWorkspace: (name?: string) => request("/api/v1/auth/workspaces", json(name ? { name } : {}), session),

  users: () => request("/api/v1/users", undefined, users),

  /** Everyone on the team, including people whose access was removed. */
  teamMembers: (signal?: AbortSignal) => request("/api/v1/users?include_inactive=true", { signal, cache: "no-store" }, users),

  /** Invite a Google account (owners and admins). No email is sent; they sign in with Google. */
  inviteMember: (body: InviteUserRequest) => request("/api/v1/users", json(body), member),

  /** Change a person's role, or remove / restore their access (owners and admins). */
  updateMember: (userId: string, body: UpdateUserRequest) =>
    request(`/api/v1/users/${userId}`, { ...json(body), method: "PATCH" }, member),

  invoices: (params: ListParams, signal?: AbortSignal) => request(`/api/v1/invoices${query(params)}`, { signal }, invoicePage),

  /** Follows cursors until the last page or `cap` invoices. */
  async allInvoices(
    params: Omit<ListParams, "cursor" | "limit">,
    { signal, cap = 5_000 }: { signal?: AbortSignal; cap?: number } = {},
  ): Promise<{ items: InvoiceRecord[]; serverTime: string; truncated: boolean }> {
    const items: InvoiceRecord[] = [];
    let cursor: string | undefined;
    let serverTime = "";
    do {
      const page = await api.invoices({ ...params, limit: PAGE_SIZE, cursor }, signal);
      if (!serverTime) serverTime = page.server_time;
      items.push(...page.items);
      cursor = page.next_cursor ?? undefined;
    } while (cursor && items.length < cap);
    return { items, serverTime, truncated: Boolean(cursor) };
  },

  audit: (invoiceId: string, signal?: AbortSignal) => request(`/api/v1/invoices/${invoiceId}/audit`, { signal }, invoice),

  resolve: (anomalyId: string, body: ResolveAnomalyRequest) => request(`/api/v1/anomalies/${anomalyId}/resolve`, json(body), resolution),

  /** Approve & archive: dismisses every open finding, approves the invoice, returns the fresh audit. */
  approve: (invoiceId: string, body: ApproveInvoiceRequest) => request(`/api/v1/invoices/${invoiceId}/approve`, json(body), invoice),

  documentUrl: (invoiceId: string) => `${BASE}/api/v1/invoices/${invoiceId}/document`,

  /**
   * Upload a document for extraction and audit. The server answers with an NDJSON stream of
   * progress events; this resolves with the audit on `complete` and rejects on `error`.
   */
  async upload(file: File, { uploadedBy, signal, onEvent }: UploadOptions = {}): Promise<{ audit: InvoiceRecord; replayed: boolean }> {
    const form = new FormData();
    form.append("file", file, file.name);
    if (uploadedBy) form.append("uploaded_by", uploadedBy);
    const response = await send(
      "/api/v1/invoices/upload",
      { method: "POST", body: form, signal, headers: { Accept: "application/x-ndjson" } },
      null,
    );
    if (!response.ok) throw await readError(response);
    if (!response.body) throw new ApiError(0, "stream_unsupported", "This browser can't read the audit progress stream.");

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += value;
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
        if (!line) continue;
        let event: UploadEvent;
        try {
          event = JSON.parse(line) as UploadEvent;
        } catch {
          throw new ApiError(0, "bad_response", "The audit progress stream was garbled. The invoice may still appear in the feed.");
        }
        onEvent?.(event);
        if (event.event === "complete") return { audit: invoice(event.audit), replayed: event.replayed };
        if (event.event === "error") throw new ApiError(422, event.error.code, event.error.message, event.error.details);
      }
      if (done) break;
    }
    throw new ApiError(0, "stream_closed", "The connection closed before the audit finished. The invoice may still appear in the feed.");
  },
};
