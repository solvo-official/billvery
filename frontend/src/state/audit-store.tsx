import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { ApiError, MAX_UPLOAD_BYTES, api, toApiError } from "@/lib/api";
import { DAY } from "@/lib/dates";
import type { Health, InvoiceRecord, Organization, Session, User, VendorScorecard } from "@/lib/types";

/** How far back the console loads history: covers the 30-day metrics, their prior period and 12-week trends. */
const HISTORY_DAYS = 90;
const POLL_INTERVAL_MS = 10_000;
/** Scorecards are re-read this long after the ledger last changed, so a burst of changes costs one request. */
const VENDOR_REFRESH_DELAY_MS = 2_000;
/** Poll from a little before the last server time so slow-committing transactions aren't missed. */
const POLL_OVERLAP_MS = 30_000;
/** The Approved Bills archive loads every approved invoice, newest first, up to this many. */
export const ARCHIVE_CAP = 5_000;
const REVIEWER_STORAGE_KEY = "invoice-auditor.reviewer";

export type Connection =
  | { status: "connecting" }
  | { status: "ready" }
  /** Nobody is signed in: the console shows its sign-in page. */
  | { status: "signed-out"; googleLogin: boolean }
  | { status: "error"; error: ApiError };

/** The archive of approved invoices loads in the background once the ledger is up. */
export type ArchiveState =
  | { status: "idle" | "loading"; error: null; truncated: boolean }
  | { status: "ready"; error: null; truncated: boolean }
  | { status: "error"; error: ApiError; truncated: boolean };

/** Vendor risk scorecards, riskiest first. They load after the ledger and follow its changes. */
export interface VendorsState {
  status: "idle" | "ready" | "error";
  list: VendorScorecard[];
  byId: Record<string, VendorScorecard>;
  error: ApiError | null;
}

const NO_VENDORS: VendorsState = { status: "idle", list: [], byId: {}, error: null };

interface AuditState {
  connection: Connection;
  session: Session | null;
  health: Health | null;
  organization: Organization | null;
  users: User[];
  /** newest first */
  invoices: InvoiceRecord[];
  /** invoice id -> time it last arrived or changed, for the tables' highlight */
  touched: Record<string, number>;
  /** invoices with a write in flight: polls must not overwrite their optimistic copy */
  pending: Record<string, true>;
  truncated: boolean;
  archive: ArchiveState;
  vendors: VendorsState;
  live: boolean;
  syncError: ApiError | null;
  lastSyncAt: number | null;
  reviewerId: string | null;
}

interface MergeOptions {
  highlight: boolean;
  /** Replace even a newer or pending copy: optimistic writes, server confirmations, rollbacks. */
  force: boolean;
}

type Action =
  | { type: "connect/start" }
  | {
      type: "connect/success";
      session: Session;
      health: Health | null;
      users: User[];
      invoices: InvoiceRecord[];
      truncated: boolean;
      reviewerId: string | null;
    }
  | { type: "connect/failure"; error: ApiError }
  | { type: "connect/signed-out"; googleLogin: boolean }
  | ({ type: "invoices/merge"; invoices: InvoiceRecord[] } & MergeOptions)
  | { type: "invoice/remove"; invoiceId: string }
  | { type: "pending/set"; invoiceId: string; pending: boolean }
  | { type: "archive/start" }
  | { type: "archive/success"; truncated: boolean }
  | { type: "archive/failure"; error: ApiError }
  | { type: "vendors/success"; list: VendorScorecard[] }
  | { type: "vendors/failure"; error: ApiError }
  | { type: "sync/result"; error: ApiError | null }
  | { type: "live/set"; live: boolean }
  | { type: "reviewer/set"; reviewerId: string }
  | { type: "users/set"; users: User[] }
  | { type: "organization/set"; organization: Organization };

const byNewest = (a: InvoiceRecord, b: InvoiceRecord) => b.created_at.localeCompare(a.created_at);
const stamp = (at: string) => Date.parse(at) || 0;

function mergeInvoices(
  current: InvoiceRecord[],
  incoming: InvoiceRecord[],
  { force = false, pending = {} }: { force?: boolean; pending?: Record<string, true> } = {},
): { invoices: InvoiceRecord[]; changed: string[] } {
  const byId = new Map(current.map((invoice) => [invoice.invoice_id, invoice]));
  const changed: string[] = [];
  for (const invoice of incoming) {
    const existing = byId.get(invoice.invoice_id);
    if (!force) {
      // An approval in flight owns its row until the server answers.
      if (pending[invoice.invoice_id]) continue;
      // Never let an older copy (e.g. from an overlapping poll) replace a newer one.
      if (existing && stamp(existing.updated_at) > stamp(invoice.updated_at)) continue;
    }
    if (!existing || existing.updated_at !== invoice.updated_at || existing.status !== invoice.status) changed.push(invoice.invoice_id);
    byId.set(invoice.invoice_id, invoice);
  }
  return { invoices: changed.length ? [...byId.values()].sort(byNewest) : current, changed };
}

const IDLE_ARCHIVE: ArchiveState = { status: "idle", error: null, truncated: false };

function reducer(state: AuditState, action: Action): AuditState {
  switch (action.type) {
    case "connect/start":
      return { ...state, connection: { status: "connecting" } };
    case "connect/success":
      return {
        ...state,
        connection: { status: "ready" },
        session: action.session,
        health: action.health,
        organization: action.session.organization,
        users: action.users,
        invoices: [...action.invoices].sort(byNewest),
        truncated: action.truncated,
        archive: IDLE_ARCHIVE,
        vendors: NO_VENDORS,
        pending: {},
        reviewerId: action.reviewerId,
        syncError: null,
        lastSyncAt: Date.now(),
      };
    case "connect/failure":
      return { ...state, connection: { status: "error", error: action.error } };
    case "connect/signed-out":
      return { ...state, connection: { status: "signed-out", googleLogin: action.googleLogin }, session: null, invoices: [] };
    case "invoices/merge": {
      const { invoices, changed } = mergeInvoices(state.invoices, action.invoices, { force: action.force, pending: state.pending });
      if (!changed.length) return state;
      const touched = action.highlight ? { ...state.touched, ...Object.fromEntries(changed.map((id) => [id, Date.now()])) } : state.touched;
      return { ...state, invoices, touched };
    }
    case "invoice/remove":
      return { ...state, invoices: state.invoices.filter((invoice) => invoice.invoice_id !== action.invoiceId) };
    case "pending/set": {
      if (Boolean(state.pending[action.invoiceId]) === action.pending) return state;
      const pending = { ...state.pending };
      if (action.pending) pending[action.invoiceId] = true;
      else delete pending[action.invoiceId];
      return { ...state, pending };
    }
    case "archive/start":
      return { ...state, archive: { status: "loading", error: null, truncated: state.archive.truncated } };
    case "archive/success":
      return { ...state, archive: { status: "ready", error: null, truncated: action.truncated } };
    case "archive/failure":
      return { ...state, archive: { status: "error", error: action.error, truncated: state.archive.truncated } };
    case "vendors/success":
      return {
        ...state,
        vendors: { status: "ready", list: action.list, byId: Object.fromEntries(action.list.map((card) => [card.vendor_id, card])), error: null },
      };
    case "vendors/failure":
      // Keep the last good scorecards on screen; the next ledger change tries again.
      return { ...state, vendors: { ...state.vendors, status: state.vendors.list.length ? "ready" : "error", error: action.error } };
    case "sync/result":
      return { ...state, syncError: action.error, lastSyncAt: action.error ? state.lastSyncAt : Date.now() };
    case "live/set":
      return { ...state, live: action.live };
    case "reviewer/set":
      return { ...state, reviewerId: action.reviewerId };
    case "users/set":
      return { ...state, users: action.users };
    case "organization/set":
      return {
        ...state,
        organization: action.organization,
        session: state.session ? { ...state.session, organization: action.organization } : state.session,
      };
  }
}

const ROLE_PREFERENCE: Record<User["role"], number> = { reviewer: 0, admin: 1, owner: 2, member: 3 };

function pickReviewer(users: User[], session: Session): string | null {
  // Signed in with Google: decisions are recorded against that person, and nobody else.
  if (session.user) return session.user.id;
  // Local development through the dev proxy's API key: pick a plausible reviewer to act as.
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(REVIEWER_STORAGE_KEY);
  } catch {
    saved = null;
  }
  const eligible = users.filter((user) => user.can_resolve);
  if (saved && eligible.some((user) => user.id === saved)) return saved;
  return [...eligible].sort((a, b) => ROLE_PREFERENCE[a.role] - ROLE_PREFERENCE[b.role] || a.name.localeCompare(b.name))[0]?.id ?? null;
}

interface AuditStore {
  connection: Connection;
  /** Who the console is authenticated as, once connected. */
  session: Session | null;
  /** The first health check, for deployment limits the UI honours (upload size). */
  health: Health | null;
  organization: Organization | null;
  users: User[];
  /** People who may resolve anomalies and approve invoices (owner, admin, reviewer). */
  reviewers: User[];
  /** Who resolutions and approvals are recorded against. */
  reviewer: User | null;
  /** Why approving isn't possible right now, if it isn't. */
  reviewBlocker: string | null;
  /** Largest file this deployment accepts for upload. */
  maxUploadBytes: number;
  /** Ends the browser session and returns to the sign-in page. */
  signOut: () => void;
  /** Re-read the active members after a team change, so names and reviewers stay current. */
  reloadUsers: () => Promise<void>;
  /** Apply saved workspace settings (name, currency) everywhere they are shown. */
  setOrganization: (organization: Organization) => void;
  invoices: InvoiceRecord[];
  touched: Record<string, number>;
  pending: Record<string, true>;
  truncated: boolean;
  archive: ArchiveState;
  vendors: VendorsState;
  /** The scorecard for a vendor on file, once loaded. */
  vendorScorecard: (vendorId: string | null | undefined) => VendorScorecard | null;
  reloadVendors: () => void;
  live: boolean;
  syncError: ApiError | null;
  lastSyncAt: number | null;
  reconnect: () => void;
  /** (Re)load every approved invoice into the ledger. */
  loadArchive: () => void;
  setLive: (live: boolean) => void;
  setReviewer: (userId: string) => void;
  /** Insert or update invoices; `highlight` marks them as just arrived, `force` overrides newer and pending copies. */
  merge: (invoices: InvoiceRecord[], options?: Partial<MergeOptions>) => void;
  remove: (invoiceId: string) => void;
  setPending: (invoiceId: string, pending: boolean) => void;
  userName: (userId: string | null) => string;
}

const AuditContext = createContext<AuditStore | null>(null);

export function AuditStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, {
    connection: { status: "connecting" },
    session: null,
    health: null,
    organization: null,
    users: [],
    invoices: [],
    touched: {},
    pending: {},
    truncated: false,
    archive: IDLE_ARCHIVE,
    vendors: NO_VENDORS,
    live: true,
    syncError: null,
    lastSyncAt: null,
    reviewerId: null,
  });
  const syncCursor = useRef<string | null>(null);
  const archiveRequest = useRef<AbortController | null>(null);
  const vendorsRequest = useRef<AbortController | null>(null);

  const connect = useCallback(async (signal?: AbortSignal) => {
    archiveRequest.current?.abort();
    dispatch({ type: "connect/start" });
    try {
      // Who are we? A 401 here is not a failure: it means nobody is signed in yet.
      const session = await api.session(signal);
      const since = new Date(Date.now() - HISTORY_DAYS * DAY).toISOString();
      const [users, recent, awaitingReview, health] = await Promise.all([
        api.users(),
        api.allInvoices({ created_since: since }, { signal }),
        // Items still in the queue stay visible however old they are.
        api.allInvoices({ status: ["NEEDS_REVIEW"] }, { signal }),
        api.health(signal).then(({ health }) => health, () => null),
      ]);
      if (signal?.aborted) return;
      syncCursor.current = recent.serverTime;
      const merged = mergeInvoices(recent.items, awaitingReview.items).invoices;
      dispatch({
        type: "connect/success",
        session,
        health,
        users,
        invoices: merged,
        truncated: recent.truncated,
        reviewerId: pickReviewer(users, session),
      });
    } catch (error) {
      if (signal?.aborted) return;
      const failure = toApiError(error);
      if (failure.status === 401) {
        const google = await api.health(signal).then(({ health }) => health.auth.google, () => false);
        dispatch({ type: "connect/signed-out", googleLogin: google });
        return;
      }
      dispatch({ type: "connect/failure", error: failure });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void connect(controller.signal);
    return () => controller.abort();
  }, [connect]);

  const loadArchive = useCallback(async () => {
    archiveRequest.current?.abort();
    const controller = new AbortController();
    archiveRequest.current = controller;
    dispatch({ type: "archive/start" });
    try {
      const approved = await api.allInvoices({ status: ["APPROVED"] }, { signal: controller.signal, cap: ARCHIVE_CAP });
      if (controller.signal.aborted) return;
      dispatch({ type: "invoices/merge", invoices: approved.items, highlight: false, force: false });
      dispatch({ type: "archive/success", truncated: approved.truncated });
    } catch (error) {
      const apiError = toApiError(error);
      if (!controller.signal.aborted && !apiError.aborted) dispatch({ type: "archive/failure", error: apiError });
    }
  }, []);

  // The archive reaches beyond the 90-day window, so it loads separately, right after the ledger.
  const ready = state.connection.status === "ready";
  const archiveIdle = state.archive.status === "idle";
  useEffect(() => {
    if (ready && archiveIdle) void loadArchive();
  }, [ready, archiveIdle, loadArchive]);
  useEffect(() => () => archiveRequest.current?.abort(), []);

  const loadVendors = useCallback(async () => {
    vendorsRequest.current?.abort();
    const controller = new AbortController();
    vendorsRequest.current = controller;
    try {
      const list = await api.vendorScorecards(controller.signal);
      if (!controller.signal.aborted) dispatch({ type: "vendors/success", list });
    } catch (error) {
      const failure = toApiError(error);
      if (!controller.signal.aborted && !failure.aborted) dispatch({ type: "vendors/failure", error: failure });
    }
  }, []);

  // Scorecards aggregate the whole ledger on the server: load them once connected, then again
  // shortly after anything in the ledger changes (a poll, an upload, a review).
  const vendorsRequested = useRef(false);
  useEffect(() => {
    if (!ready) {
      vendorsRequested.current = false;
      return;
    }
    const timer = window.setTimeout(() => void loadVendors(), vendorsRequested.current ? VENDOR_REFRESH_DELAY_MS : 0);
    vendorsRequested.current = true;
    return () => window.clearTimeout(timer);
  }, [ready, state.invoices, loadVendors]);
  useEffect(() => () => vendorsRequest.current?.abort(), []);

  // Live feed: poll for anything created or changed since the last sync.
  useEffect(() => {
    if (!ready || !state.live) return;
    let cancelled = false;
    let timer = 0;
    const controller = new AbortController();
    const tick = async () => {
      if (!document.hidden && syncCursor.current) {
        try {
          const since = new Date(new Date(syncCursor.current).getTime() - POLL_OVERLAP_MS).toISOString();
          const changes = await api.allInvoices({ updated_since: since }, { signal: controller.signal, cap: 2_000 });
          if (cancelled) return;
          syncCursor.current = changes.serverTime;
          dispatch({ type: "invoices/merge", invoices: changes.items, highlight: true, force: false });
          dispatch({ type: "sync/result", error: null });
        } catch (error) {
          if (cancelled) return;
          const failure = toApiError(error);
          if (failure.status === 401) {
            // The session expired while the console was open.
            dispatch({ type: "connect/signed-out", googleLogin: true });
            return;
          }
          dispatch({ type: "sync/result", error: failure });
        }
      }
      if (!cancelled) timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
    };
    timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [ready, state.live]);

  const merge = useCallback(
    (invoices: InvoiceRecord[], options?: Partial<MergeOptions>) =>
      dispatch({ type: "invoices/merge", invoices, highlight: options?.highlight ?? true, force: options?.force ?? false }),
    [],
  );
  const remove = useCallback((invoiceId: string) => dispatch({ type: "invoice/remove", invoiceId }), []);
  const setPending = useCallback((invoiceId: string, pending: boolean) => dispatch({ type: "pending/set", invoiceId, pending }), []);

  const store = useMemo<AuditStore>(() => {
    const names = new Map(state.users.map((user) => [user.id, user.name]));
    const reviewers = state.users.filter((user) => user.can_resolve);
    const reviewer = reviewers.find((user) => user.id === state.reviewerId) ?? null;
    return {
      connection: state.connection,
      session: state.session,
      health: state.health,
      organization: state.organization,
      users: state.users,
      reviewers,
      reviewer,
      reviewBlocker: reviewer
        ? null
        : state.session?.user
          ? "Your role can view invoices but not approve them."
          : "Choose who you're acting as in the top-right menu.",
      maxUploadBytes: state.health?.limits.max_upload_bytes ?? MAX_UPLOAD_BYTES,
      setOrganization: (organization) => dispatch({ type: "organization/set", organization }),
      reloadUsers: async () => {
        try {
          dispatch({ type: "users/set", users: await api.users() });
        } catch {
          // The next reconnect refreshes the list; team changes themselves already succeeded.
        }
      },
      signOut: () => {
        void api.logout().finally(() => window.location.reload());
      },
      invoices: state.invoices,
      touched: state.touched,
      pending: state.pending,
      truncated: state.truncated,
      archive: state.archive,
      vendors: state.vendors,
      vendorScorecard: (vendorId) => (vendorId ? state.vendors.byId[vendorId] ?? null : null),
      reloadVendors: () => void loadVendors(),
      live: state.live,
      syncError: state.syncError,
      lastSyncAt: state.lastSyncAt,
      reconnect: () => void connect(),
      loadArchive: () => void loadArchive(),
      setLive: (live) => dispatch({ type: "live/set", live }),
      setReviewer: (userId) => {
        try {
          window.localStorage.setItem(REVIEWER_STORAGE_KEY, userId);
        } catch {
          // Private windows can refuse storage; the choice then lasts for this visit only.
        }
        dispatch({ type: "reviewer/set", reviewerId: userId });
      },
      merge,
      remove,
      setPending,
      userName: (userId) => (userId ? names.get(userId) ?? `User ${userId.slice(0, 8)}` : "—"),
    };
  }, [state, connect, loadArchive, loadVendors, merge, remove, setPending]);

  return <AuditContext value={store}>{children}</AuditContext>;
}

export function useAudit(): AuditStore {
  const store = useContext(AuditContext);
  if (!store) throw new Error("useAudit() outside <AuditStoreProvider>");
  return store;
}

/** The organization, once connected. Components under the ready gate can rely on it. */
export function useOrganization(): Organization {
  const { organization } = useAudit();
  if (!organization) throw new Error("useOrganization() before the API connection is ready");
  return organization;
}
