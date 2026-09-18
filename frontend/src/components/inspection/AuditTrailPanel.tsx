import {
  ArrowRight,
  CircleCheck,
  CircleX,
  FileCheck2,
  FileInput,
  FileSearch,
  Fingerprint,
  LoaderCircle,
  RotateCw,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Stamp,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { StatusBadge } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime } from "@/lib/dates";
import { formatBytes } from "@/lib/format";
import { ANOMALY_INFO } from "@/lib/rules";
import type { AnomalyType, AuditAction, AuditLogEntry, IntegrityReport, InvoiceRecord } from "@/lib/types";

const ACTION: Record<AuditAction, { label: string; icon: LucideIcon; tone: string }> = {
  INGESTED: { label: "Received and audited", icon: FileInput, tone: "text-ink-2 bg-ink-3/10 ring-line" },
  ANOMALY_DISMISSED: { label: "Flag dismissed", icon: CircleCheck, tone: "text-approved bg-approved/10 ring-approved/20" },
  ANOMALY_CONFIRMED: { label: "Flag confirmed", icon: CircleX, tone: "text-rejected bg-rejected/10 ring-rejected/20" },
  APPROVED: { label: "Approved & archived", icon: Stamp, tone: "text-approved bg-approved/10 ring-approved/20" },
};

const INTEGRITY: Record<IntegrityReport["status"], { title: string; icon: LucideIcon; tone: string }> = {
  verified: { title: "Document intact", icon: ShieldCheck, tone: "border-approved/30 bg-approved/[0.06] text-approved" },
  tampered: { title: "Tampering detected", icon: ShieldAlert, tone: "border-rejected/35 bg-rejected/[0.07] text-rejected" },
  unavailable: { title: "Original not stored here", icon: ShieldQuestion, tone: "border-line bg-sunken text-ink-2" },
};

async function sha256Hex(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

type LocalCheck = { status: "hashing"; name: string } | { status: "done"; name: string; size: number; sha256: string; match: boolean } | { status: "error"; name: string; message: string };

/**
 * The invoice's chain of custody and its tamper checks: the server re-hashes the stored original,
 * and a reviewer can compare any local copy (e.g. the PDF from the vendor's email) in the browser.
 */
export function AuditTrailPanel({ invoice, now }: { invoice: InvoiceRecord; now: number }) {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [integrity, setIntegrity] = useState<IntegrityReport | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<ApiError | null>(null);
  const [local, setLocal] = useState<LocalCheck | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const id = invoice.invoice_id;

  // Re-read whenever the invoice changes: a commit or approval adds an entry.
  const load = useCallback(
    (signal?: AbortSignal) =>
      api.auditTrail(id, signal).then(
        (list) => {
          setEntries(list);
          setLoadError(null);
        },
        (error) => {
          const failure = toApiError(error);
          if (!failure.aborted) setLoadError(failure);
        },
      ),
    [id],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, invoice.updated_at]);

  const verify = async () => {
    setVerifying(true);
    setVerifyError(null);
    try {
      setIntegrity(await api.integrity(id));
    } catch (error) {
      setVerifyError(toApiError(error));
    } finally {
      setVerifying(false);
    }
  };

  const compareLocal = async (file: File) => {
    setLocal({ status: "hashing", name: file.name });
    try {
      const sha256 = await sha256Hex(file);
      setLocal({ status: "done", name: file.name, size: file.size, sha256, match: sha256 === invoice.document.sha256 });
    } catch {
      setLocal({ status: "error", name: file.name, message: "This browser couldn't read the file." });
    }
  };

  const verdict = integrity ? INTEGRITY[integrity.status] : null;

  return (
    <section aria-labelledby="custody-heading" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id="custody-heading" className="eyebrow">
          Audit trail
        </h3>
        <Tooltip content={<span className="figure break-all">SHA-256 recorded at ingestion: {invoice.document.sha256}</span>}>
          <span className="figure flex cursor-default items-center gap-1 text-[11px] text-ink-3" tabIndex={0}>
            <Fingerprint className="size-3" aria-hidden />
            {invoice.document.sha256.slice(0, 12)}…
          </span>
        </Tooltip>
      </div>

      <div className="rounded-lg border border-line">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2.5">
          <Button size="sm" variant="secondary" onClick={() => void verify()} disabled={verifying}>
            {verifying ? <LoaderCircle className="animate-spin" aria-hidden /> : <FileCheck2 aria-hidden />}
            {verifying ? "Verifying…" : integrity ? "Verify again" : "Verify document"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => fileInput.current?.click()} disabled={local?.status === "hashing"}>
            <FileSearch aria-hidden /> Compare a local copy
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,image/png,image/jpeg"
            className="sr-only"
            tabIndex={-1}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void compareLocal(file);
            }}
          />
          <span className="ml-auto text-[11px] text-ink-3">SHA-256, re-computed on demand</span>
        </div>

        {verdict && integrity ? (
          <div role="status" className={cn("flex items-start gap-2.5 border-b px-3 py-2.5 text-[12px]", verdict.tone)}>
            <verdict.icon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p className="min-w-0 leading-relaxed text-ink-2">
              <span className="font-medium text-ink">{verdict.title}.</span> {integrity.message}
              {integrity.computed_sha256 && integrity.status === "tampered" ? (
                <span className="figure mt-1 block break-all text-[11px] text-ink-3">
                  stored now {integrity.computed_sha256} · recorded {integrity.recorded_sha256}
                </span>
              ) : null}
              <span className="mt-0.5 block text-[11px] text-ink-3">
                Checked {relativeTime(integrity.checked_at, now)}
                {integrity.computed_size_bytes !== null ? ` · ${formatBytes(integrity.computed_size_bytes)} hashed on the server` : ""}
              </span>
            </p>
          </div>
        ) : null}
        {verifyError ? (
          <p role="alert" className="border-b border-line px-3 py-2.5 text-[12px] text-rejected">
            Couldn't verify: {verifyError.message}
          </p>
        ) : null}
        {local ? <LocalResult check={local} /> : null}

        <Timeline entries={entries} error={loadError} onRetry={() => void load()} now={now} />
      </div>
    </section>
  );
}

function LocalResult({ check }: { check: LocalCheck }) {
  if (check.status === "hashing") {
    return (
      <p role="status" className="flex items-center gap-2 border-b border-line px-3 py-2.5 text-[12px] text-ink-2">
        <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> Hashing {check.name}…
      </p>
    );
  }
  if (check.status === "error") {
    return (
      <p role="alert" className="border-b border-line px-3 py-2.5 text-[12px] text-rejected">
        {check.name}: {check.message}
      </p>
    );
  }
  return (
    <p
      role="status"
      className={cn(
        "flex items-start gap-2.5 border-b px-3 py-2.5 text-[12px] leading-relaxed",
        check.match ? "border-approved/30 bg-approved/[0.06]" : "border-rejected/35 bg-rejected/[0.07]",
      )}
    >
      {check.match ? <ShieldCheck className="mt-0.5 size-4 shrink-0 text-approved" aria-hidden /> : <ShieldAlert className="mt-0.5 size-4 shrink-0 text-rejected" aria-hidden />}
      <span className="min-w-0 text-ink-2">
        <span className="font-medium text-ink">{check.match ? "Same file." : "Different file."}</span>{" "}
        <span className="break-all">{check.name}</span> ({formatBytes(check.size)}){" "}
        {check.match ? "is byte-for-byte the document that was audited." : "doesn't match the audited document. It was changed, or it's another file."}
        <span className="figure mt-0.5 block break-all text-[11px] text-ink-3">sha256 {check.sha256}</span>
      </span>
    </p>
  );
}

function Timeline({ entries, error, onRetry, now }: { entries: AuditLogEntry[] | null; error: ApiError | null; onRetry: () => void; now: number }) {
  if (error && !entries) {
    return (
      <div className="flex items-center gap-3 px-3 py-3 text-[12px] text-ink-2">
        <span className="min-w-0 flex-1">Couldn't load the audit trail. {error.message}</span>
        <Button size="xs" variant="secondary" onClick={onRetry}>
          <RotateCw aria-hidden /> Retry
        </Button>
      </div>
    );
  }
  if (!entries) {
    return (
      <p role="status" className="flex items-center gap-2 px-3 py-3 text-[12px] text-ink-3">
        <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> Loading the audit trail…
      </p>
    );
  }
  if (entries.length === 0) {
    return <p className="px-3 py-3 text-[12px] text-ink-3">No entries yet.</p>;
  }
  return (
    <ol className="flex flex-col px-3 py-1" aria-label="Audit trail, oldest first">
      {entries.map((entry, index) => (
        <TrailEntry key={entry.id} entry={entry} last={index === entries.length - 1} now={now} />
      ))}
    </ol>
  );
}

function TrailEntry({ entry, last, now }: { entry: AuditLogEntry; last: boolean; now: number }) {
  const action = ACTION[entry.action];
  const Icon = action.icon;
  const anomalyType = entry.details.anomaly_type as AnomalyType | undefined;
  const dismissed = Array.isArray(entry.details.dismissed_anomalies) ? entry.details.dismissed_anomalies.length : null;
  const signer = entry.user_display_name ?? (entry.action === "INGESTED" ? "API integration" : "Unknown");
  const backfilled = entry.details.backfilled === true;

  return (
    <li className="relative flex gap-3 py-2.5">
      {!last ? <span className="absolute bottom-0 left-[11px] top-9 w-px bg-line" aria-hidden /> : null}
      <span className={cn("relative flex size-6 shrink-0 items-center justify-center rounded-full ring-1 ring-inset", action.tone)} aria-hidden>
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
          <span className="font-medium text-ink">{action.label}</span>
          {anomalyType && ANOMALY_INFO[anomalyType] ? <span className="text-ink-3">{ANOMALY_INFO[anomalyType].label}</span> : null}
          {dismissed ? <span className="text-ink-3">{dismissed} open flag{dismissed === 1 ? "" : "s"} dismissed</span> : null}
          {entry.new_status ? (
            <span className="flex items-center gap-1">
              {entry.previous_status ? (
                <>
                  <StatusBadge status={entry.previous_status} className="h-[18px] text-[11px]" />
                  <ArrowRight className="size-3 text-ink-3" aria-label="to" />
                </>
              ) : null}
              <StatusBadge status={entry.new_status} className="h-[18px] text-[11px]" />
            </span>
          ) : null}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-ink-3">
          <span className="text-ink-2">{signer}</span>
          {entry.user_id ? (
            <Tooltip content={<span className="figure">user {entry.user_id}</span>}>
              <span className="figure cursor-default rounded-[4px] border border-line px-1 text-[10.5px]" tabIndex={0}>
                {entry.user_id.slice(0, 8)}
              </span>
            </Tooltip>
          ) : null}
          <span aria-hidden>·</span>
          <Tooltip content={<span className="figure">{formatDateTime(entry.created_at)}</span>}>
            <time dateTime={entry.created_at} className="cursor-default" tabIndex={0}>
              {relativeTime(entry.created_at, now)}
            </time>
          </Tooltip>
          {backfilled ? (
            <Tooltip content="Recorded before the audit trail existed; rebuilt from the finding's resolution when it was introduced.">
              <span className="cursor-default rounded-full bg-ink-3/10 px-1.5 text-[10.5px]" tabIndex={0}>
                backfilled
              </span>
            </Tooltip>
          ) : null}
        </p>
        {entry.resolution_note ? (
          <blockquote className="mt-1.5 border-l-2 border-line-strong pl-2.5 text-[12px] leading-snug text-ink-2">{entry.resolution_note}</blockquote>
        ) : null}
      </div>
    </li>
  );
}
