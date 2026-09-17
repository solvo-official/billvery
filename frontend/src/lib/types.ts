/**
 * The audit API's contract (FastAPI, /api/v1). Field names and shapes match the JSON exactly:
 * snake_case, money as decimal strings, timestamps as ISO 8601.
 */

/** Decimal string as the API returns it, e.g. "1050.00" or "1050.125". Never a float. */
export type Amount = string;
export type Uuid = string;
/** YYYY-MM-DD */
export type IsoDate = string;
/** ISO 8601 timestamp with offset */
export type IsoDateTime = string;

export const INVOICE_STATUSES = ["PROCESSING", "NEEDS_REVIEW", "APPROVED", "REJECTED"] as const;
export type InvoiceStatus = (typeof INVOICE_STATUSES)[number];
export type SettledStatus = Exclude<InvoiceStatus, "PROCESSING">;

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type AnomalyStatus = "OPEN" | "DISMISSED" | "CONFIRMED";
export type Resolution = Exclude<AnomalyStatus, "OPEN">;

export type AnomalyType =
  | "DUPLICATE_FILE_HASH"
  | "DUPLICATE_INVOICE_NUMBER"
  | "SIMILAR_INVOICE_NUMBER"
  | "VENDOR_TAX_ID_MISMATCH"
  | "VENDOR_NAME_MISMATCH"
  | "SIMILAR_VENDOR_NAME"
  | "MATH_TOTAL_MISMATCH"
  | "PRICE_SPIKE"
  | "FUTURE_INVOICE_DATE"
  | "STALE_INVOICE_DATE"
  | "MISSING_REQUIRED_FIELDS"
  | "TAX_RATE_EXCEEDED"
  | "LOW_EXTRACTION_CONFIDENCE";

/** AnomalyOut */
export interface Anomaly {
  id: Uuid;
  type: AnomalyType;
  severity: Severity;
  /** 0-100: how likely the finding is a real problem */
  confidence: number;
  description: string;
  detected_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
  status: AnomalyStatus;
  resolved_by: Uuid | null;
  resolved_at: IsoDateTime | null;
  resolution_note: string | null;
  created_at: IsoDateTime;
}

export interface FinancialSummary {
  currency: string;
  subtotal: Amount | null;
  tax: Amount;
  shipping: Amount;
  discount: Amount;
  total: Amount | null;
}

export interface VendorSummary {
  id: Uuid | null;
  name: string | null;
  tax_id: string | null;
  confidence: number | null;
}

export interface VendorOnFile {
  id: Uuid;
  name: string;
  /** normalized: upper-case alphanumerics */
  tax_id: string | null;
  /** the vendor record was created by this invoice */
  first_seen: boolean;
}

export interface LineItem {
  line_number: number;
  description: string | null;
  quantity: Amount;
  unit_price: Amount;
  line_total: Amount;
}

export interface DocumentInfo {
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  storage_url: string | null;
  /** the original can be fetched from GET /api/v1/invoices/{id}/document */
  available: boolean;
}

export interface UserRef {
  id: Uuid;
  name: string;
}

/** ApprovalOut: who approved the invoice and when. `by` is null for automatic approvals. */
export interface Approval {
  at: IsoDateTime;
  by: UserRef | null;
  /** automatic: the rule engine approved it; reviewer: a person did (Approve & Archive or flag decisions) */
  method: "automatic" | "reviewer";
}

/** InvoiceAuditResponse: GET /api/v1/invoices/{id}/audit and each item of GET /api/v1/invoices */
export interface InvoiceRecord {
  invoice_id: Uuid;
  status: InvoiceStatus;
  invoice_number: string | null;
  invoice_date: IsoDate | null;
  due_date: IsoDate | null;
  vendor: VendorSummary;
  vendor_on_file: VendorOnFile | null;
  financial_summary: FinancialSummary;
  line_items: LineItem[];
  ai_confidence: number | null;
  anomalies: Anomaly[];
  document: DocumentInfo;
  /** upload: submitted by a person; api: by an integration */
  source: "upload" | "api";
  submitted_by: UserRef | null;
  /** set exactly when status is APPROVED */
  approval: Approval | null;
  processed_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface InvoicePage {
  items: InvoiceRecord[];
  next_cursor: string | null;
  server_time: IsoDateTime;
}

export interface Organization {
  id: Uuid;
  name: string;
  legal_name: string | null;
  country_code: string | null;
  currency_code: string;
  subscription_plan: string;
}

export type Role = "owner" | "admin" | "reviewer" | "member";

export interface User {
  id: Uuid;
  name: string;
  email: string;
  role: Role;
  can_resolve: boolean;
  /** false once an owner or admin removed the person's access */
  active: boolean;
  /** last Google sign-in; null for an invited person who hasn't signed in yet */
  last_login_at: IsoDateTime | null;
}

/** POST /api/v1/users: the Google account can sign in as soon as this succeeds. */
export interface InviteUserRequest {
  email: string;
  role: Role;
}

/** A workspace the signed-in Google account belongs to (GET /api/v1/auth/workspaces). */
export interface Workspace {
  organization: Organization;
  /** null when the console authenticated with the development API key */
  role: Role | null;
  current: boolean;
}

/** PATCH /api/v1/organization (owners and admins) */
export interface UpdateOrganizationRequest {
  name?: string;
  legal_name?: string | null;
  country_code?: string | null;
  currency_code?: string;
}

/** PATCH /api/v1/users/{id} */
export interface UpdateUserRequest {
  role?: Role;
  active?: boolean;
}

export interface Health {
  status: "ok";
  version: string;
  database: "ok";
  extraction: { configured: boolean; model: string };
  /** Which sign-in methods this deployment offers. */
  auth: { google: boolean; session: boolean };
  limits: { max_upload_bytes: number };
}

/** GET /api/v1/auth/session: who the caller is. */
export interface Session {
  /** session: a person signed in with Google. api_key: the dev proxy's key (local development). */
  method: "session" | "api_key";
  user: User | null;
  organization: Organization;
}

export interface ResolveAnomalyRequest {
  resolution: Resolution;
  resolved_by: Uuid;
  note: string | null;
}

export interface ResolveAnomalyResponse {
  anomaly: Anomaly;
  invoice_id: Uuid;
  invoice_status: InvoiceStatus;
}

/** POST /api/v1/invoices/{id}/approve: dismisses every open finding and approves the invoice. */
export interface ApproveInvoiceRequest {
  approved_by: Uuid;
  /** recorded on each dismissed finding; the server defaults to "Approved and archived." */
  note: string | null;
}

/** One line of the NDJSON stream from POST /api/v1/invoices/upload */
export type UploadEvent =
  | { event: "received"; filename: string; content_type: string; size_bytes: number; sha256: string }
  | { event: "extracting"; model: string; elapsed_ms: number; reused?: boolean }
  | { event: "auditing" }
  | { event: "complete"; replayed: boolean; audit: InvoiceRecord }
  | { event: "error"; error: { code: string; message: string; details?: unknown } };

/** Regions of the reconstructed document that a finding points at. */
export type DocField =
  | "vendor"
  | "tax_id"
  | "invoice_number"
  | "invoice_date"
  | "due_date"
  | "subtotal"
  | "tax"
  | "shipping"
  | "discount"
  | "total"
  | `line:${number}`;
