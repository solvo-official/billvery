import { ArrowRight, Check, CircleSlash, FileImage, FileText, FileUp, Hourglass, LoaderCircle, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { StatusBadge } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { useNow } from "@/hooks/use-now";
import { ACCEPTED_TYPES, api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { DAY, relativeTime } from "@/lib/dates";
import { formatBytes as formatSize } from "@/lib/format";
import type { InvoiceRecord, InvoiceStatus, Organization, User } from "@/lib/types";
import { useAudit, useOrganization } from "@/state/audit-store";

/** throttled: every Gemini API key was rate limited; the upload starts again by itself shortly. */
type Phase = "uploading" | "extracting" | "throttled" | "auditing" | "complete" | "failed" | "cancelled";

interface UploadJob {
  id: string;
  name: string;
  size: number;
  type: string;
  phase: Phase;
  file?: File;
  extractingSince?: number;
  reused?: boolean;
  keySwitches?: number;
  /** While every API key is resting: when the server expects one back (Date.now() clock). */
  keyWaitUntil?: number;
  /** In the throttled phase: when the upload starts again. */
  retryAt?: number;
  /** Automatic restarts after throttling so far. */
  retries?: number;
  retryable?: boolean;
  model?: string;
  sha?: string;
  invoiceId?: string;
  result?: InvoiceStatus;
  openFlags?: number;
  replayed?: boolean;
  error?: string;
  controller?: AbortController;
}

const STEPS: { phase: Phase; label: string }[] = [
  { phase: "uploading", label: "Upload" },
  { phase: "extracting", label: "Extract" },
  { phase: "auditing", label: "Verify" },
  { phase: "complete", label: "Complete" },
];

const PHASE_LABEL: Partial<Record<Phase, string>> = {
  uploading: "Uploading…",
  extracting: "Extracting with Gemini Vision…",
  throttled: "Throttled: waiting for an API key…",
  auditing: "Verifying Line Math…",
  complete: "Audit Complete",
};

/** A throttled upload restarts by itself this many times before it's reported as failed. */
const AUTO_RETRIES = 3;
// Uploads that failed together shouldn't all come back in the same instant.
const RETRY_SPREAD_MS = 4_000;

/** Server-side failures worth offering a Retry for; bad files (type, size) are not. */
const RETRYABLE_CODES = new Set(["extraction_throttled", "extraction_failed", "internal_error", "stream_closed", "timeout", "bad_response"]);

function throttling(error: ApiError): { retryAfterMs: number; daily: boolean } | null {
  if (error.code !== "extraction_throttled") return null;
  const details = (error.details ?? {}) as { retry_after_ms?: unknown; daily_quota?: unknown };
  const retryAfterMs = typeof details.retry_after_ms === "number" && details.retry_after_ms > 0 ? details.retry_after_ms : 30_000;
  return { retryAfterMs, daily: details.daily_quota === true };
}

const acceptedType = (file: File) =>
  file.type in ACCEPTED_TYPES || /\.(pdf|png|jpe?g)$/i.test(file.name);

/** What the feed shows for an upload the server hasn't finished auditing yet. */
function placeholder(id: string, file: File, organization: Organization, reviewer: User | null): InvoiceRecord {
  const now = new Date().toISOString();
  return {
    invoice_id: id,
    status: "PROCESSING",
    invoice_number: null,
    invoice_date: null,
    due_date: null,
    vendor: { id: null, name: null, tax_id: null, confidence: null },
    vendor_on_file: null,
    financial_summary: { currency: organization.currency_code, subtotal: null, tax: "0.00", shipping: "0.00", discount: "0.00", total: null },
    line_items: [],
    ai_confidence: null,
    anomalies: [],
    document: { filename: file.name, content_type: file.type, size_bytes: file.size, sha256: "", storage_url: null, available: false },
    source: "upload",
    submitted_by: reviewer ? { id: reviewer.id, name: reviewer.name } : null,
    approval: null,
    processed_at: null,
    created_at: now,
    updated_at: now,
  };
}

export function IngestionDropzone({ onInspect }: { onInspect: (invoiceId: string) => void }) {
  const { invoices, merge, remove, reviewer, maxUploadBytes } = useAudit();
  const organization = useOrganization();
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const patch = (id: string, changes: Partial<UploadJob>) => setJobs((all) => all.map((job) => (job.id === id ? { ...job, ...changes } : job)));
  const retryTimers = useRef(new Map<string, number>());

  /** Uploads a file for a job already in the list; again after throttling, with `retries` counting up. */
  const run = useCallback(
    (id: string, file: File, retries: number) => {
      const controller = new AbortController();
      patch(id, {
        phase: "uploading",
        controller,
        retries,
        retryAt: undefined,
        error: undefined,
        retryable: false,
        extractingSince: undefined,
        keySwitches: 0,
        keyWaitUntil: undefined,
      });
      const pendingId = `upload:${id}`;
      merge([placeholder(pendingId, file, organization, reviewer)]);

      api
        .upload(file, {
          uploadedBy: reviewer?.id,
          signal: controller.signal,
          onEvent: (event) => {
            if (event.event === "received") patch(id, { sha: event.sha256 });
            if (event.event === "extracting") {
              const waiting = event.waiting_ms ?? 0;
              setJobs((all) =>
                all.map((j) =>
                  j.id === id
                    ? {
                        ...j,
                        phase: "extracting",
                        model: event.model,
                        reused: event.reused ?? j.reused,
                        extractingSince: j.extractingSince ?? Date.now(),
                        keySwitches: event.key_switches ?? j.keySwitches,
                        keyWaitUntil: waiting > 0 ? Date.now() + waiting : undefined,
                      }
                    : j,
                ),
              );
            }
            if (event.event === "auditing") patch(id, { phase: "auditing", keyWaitUntil: undefined });
          },
        })
        .then(({ audit, replayed }) => {
          remove(pendingId);
          merge([audit]);
          patch(id, {
            phase: "complete",
            invoiceId: audit.invoice_id,
            result: audit.status,
            openFlags: audit.anomalies.filter((a) => a.status === "OPEN").length,
            replayed,
            controller: undefined,
            file: undefined,
          });
        })
        .catch((error: unknown) => {
          const apiError = toApiError(error);
          if (apiError.code === "aborted") {
            remove(pendingId);
            patch(id, { phase: "cancelled", controller: undefined, file: undefined });
            return;
          }
          const throttle = throttling(apiError);
          if (throttle && !throttle.daily && retries < AUTO_RETRIES) {
            // Every key is resting: keep the batch going by starting this upload again once one is back.
            const delay = throttle.retryAfterMs + Math.round(Math.random() * RETRY_SPREAD_MS);
            patch(id, { phase: "throttled", retryAt: Date.now() + delay, controller: undefined, keyWaitUntil: undefined });
            retryTimers.current.set(
              id,
              window.setTimeout(() => {
                retryTimers.current.delete(id);
                runRef.current(id, file, retries + 1);
              }, delay),
            );
            return;
          }
          remove(pendingId);
          patch(id, {
            phase: "failed",
            error: apiError.message,
            controller: undefined,
            retryable: RETRYABLE_CODES.has(apiError.code) || apiError.unreachable,
          });
        });
    },
    [merge, remove, organization, reviewer],
  );
  const runRef = useRef(run);
  runRef.current = run;

  const cancel = (job: UploadJob) => {
    const timer = retryTimers.current.get(job.id);
    if (timer === undefined) {
      job.controller?.abort();
      return;
    }
    window.clearTimeout(timer);
    retryTimers.current.delete(job.id);
    remove(`upload:${job.id}`);
    patch(job.id, { phase: "cancelled", retryAt: undefined, file: undefined });
  };

  const retry = (job: UploadJob) => {
    if (job.file) run(job.id, job.file, 0);
  };

  const start = useCallback(
    (file: File) => {
      const id = crypto.randomUUID();
      const job: UploadJob = { id, name: file.name, size: file.size, type: file.type, phase: "uploading" };
      if (!acceptedType(file)) {
        job.phase = "failed";
        job.error = "Can't audit this file type. Upload a PDF, PNG or JPEG.";
      } else if (file.size > maxUploadBytes) {
        job.phase = "failed";
        job.error = `This file is ${formatSize(file.size)}. The limit here is ${formatSize(maxUploadBytes)}; export a smaller PDF.`;
      } else if (file.size === 0) {
        job.phase = "failed";
        job.error = "This file is empty.";
      }
      if (job.phase === "failed") {
        setJobs((all) => [job, ...all].slice(0, 8));
        return;
      }

      job.file = file;
      setJobs((all) => [job, ...all].slice(0, 8));
      run(id, file, 0);
    },
    [run, maxUploadBytes],
  );

  // Cancel in-flight uploads and pending restarts if the console unmounts.
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  useEffect(
    () => () => {
      retryTimers.current.forEach((timer) => window.clearTimeout(timer));
      jobsRef.current.forEach((job) => job.controller?.abort());
    },
    [],
  );

  const startMany = (files: FileList | null) => {
    if (files) Array.from(files).forEach(start);
  };

  // This reviewer's uploads from the last day that came in before this visit.
  const recent = useMemo(() => {
    const cutoff = Date.now() - DAY;
    const shown = new Set(jobs.map((job) => job.invoiceId));
    return invoices
      .filter(
        (invoice) =>
          invoice.source === "upload" &&
          invoice.status !== "PROCESSING" &&
          reviewer !== null &&
          invoice.submitted_by?.id === reviewer.id &&
          new Date(invoice.created_at).getTime() > cutoff &&
          !shown.has(invoice.invoice_id),
      )
      .slice(0, 3);
  }, [invoices, jobs, reviewer]);

  const onDragOver = (event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDragging(true);
  };
  const onDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    startMany(event.dataTransfer.files);
  };

  return (
    <section aria-labelledby="ingest-heading" className="glass overflow-hidden rounded-xl">
      <div className="flex items-baseline justify-between px-4 pb-3 pt-4">
        <h2 id="ingest-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
          Ingest documents
        </h2>
        <span className="font-mono text-[11px] text-ink-3">
          {Object.values(ACCEPTED_TYPES).join(" · ")} · ≤{formatSize(maxUploadBytes)}
        </span>
      </div>

      <div className="px-4 pb-4">
        <div
          onDragEnter={onDragOver}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={cn(
            "relative rounded-lg border border-dashed transition-[border-color,background-color,transform] duration-200",
            dragging ? "scale-[1.01] border-accent bg-accent/[0.07]" : "border-line-strong bg-sunken/60 hover:border-ink-3 hover:bg-sunken",
          )}
        >
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="group flex w-full cursor-pointer flex-col items-center gap-2 rounded-lg px-4 py-6 text-center outline-offset-[-2px]"
          >
            <span
              className={cn(
                "flex size-10 items-center justify-center rounded-xl border bg-surface-solid shadow-[var(--shadow-card)] transition-transform duration-200 group-hover:-translate-y-0.5",
                dragging ? "-translate-y-0.5 border-accent/60 text-accent" : "border-line text-ink-2",
              )}
            >
              <FileUp className="size-4" aria-hidden />
            </span>
            <span className="text-[13px] font-medium text-ink">{dragging ? "Release to start the audit" : "Drop invoices here or browse"}</span>
            <span className="max-w-[28ch] text-[12px] text-ink-3">
              Gemini reads each file on the server, then every audit rule runs against your ledger.
            </span>
          </button>
          <input
            ref={inputRef}
            id="document-upload"
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            className="sr-only"
            tabIndex={-1}
            onChange={(event) => {
              startMany(event.target.files);
              event.target.value = "";
            }}
          />
        </div>
        {reviewer ? (
          <p className="mt-2 text-[11px] text-ink-3">
            Uploaded as <span className="text-ink-2">{reviewer.name}</span>
          </p>
        ) : null}
      </div>

      {jobs.length > 0 || recent.length > 0 ? (
        <div className="border-t border-line">
          <h3 className="eyebrow px-4 pb-1 pt-3">Your recent uploads</h3>
          <ul className="divide-y divide-line">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} onInspect={onInspect} onCancel={cancel} onRetry={retry} />
            ))}
            {recent.map((invoice) => (
              <LedgerUploadRow key={invoice.invoice_id} invoice={invoice} onInspect={onInspect} />
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function FileIcon({ type }: { type: string }) {
  const Icon = type.startsWith("image/") ? FileImage : FileText;
  return <Icon className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />;
}

function JobRow({
  job,
  onInspect,
  onCancel,
  onRetry,
}: {
  job: UploadJob;
  onInspect: (id: string) => void;
  onCancel: (job: UploadJob) => void;
  onRetry: (job: UploadJob) => void;
}) {
  const throttled = job.phase === "throttled";
  const running = job.phase === "uploading" || job.phase === "extracting" || throttled || job.phase === "auditing";
  const now = useNow(job.phase === "extracting" || throttled ? 1_000 : 60_000);
  const stepIndex = STEPS.findIndex((step) => step.phase === (throttled ? "extracting" : job.phase));
  const elapsed = job.extractingSince ? Math.max(0, Math.round((now - job.extractingSince) / 1000)) : 0;
  const width = job.phase === "uploading" ? 12 : job.phase === "auditing" ? 92 : throttled ? 40 : 100;
  const secondsUntil = (at: number | undefined) => (at === undefined ? 0 : Math.max(0, Math.ceil((at - now) / 1000)));
  const keyWait = job.phase === "extracting" ? secondsUntil(job.keyWaitUntil) : 0;

  let status: string | undefined = PHASE_LABEL[job.phase];
  let paced = false;
  if (throttled) {
    const seconds = secondsUntil(job.retryAt);
    const attempt = `retry ${(job.retries ?? 0) + 1} of ${AUTO_RETRIES}`;
    status = `Throttled: every API key is at its rate limit. ${seconds > 0 ? `Trying again in ${seconds}s` : "Trying again now"} (${attempt})`;
    paced = true;
  } else if (job.phase === "extracting" && job.reused) {
    status = "Reusing the extraction of an identical file…";
  } else if (keyWait > 0) {
    status = `Every API key is busy. Waiting ${keyWait}s for one…`;
    paced = true;
  } else if (job.phase === "extracting" && (job.keySwitches ?? 0) > 0) {
    status = "Rate limit hit, continuing on another API key…";
    paced = true;
  }

  return (
    <li className="px-4 py-3">
      <div className="flex items-start gap-2.5">
        <FileIcon type={job.type} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <p className="truncate text-[13px] text-ink" title={job.name}>
              {job.name}
            </p>
            <span className="figure shrink-0 text-[11px] text-ink-3">{formatSize(job.size)}</span>
          </div>

          {job.phase === "failed" ? (
            <div className="mt-1 flex items-start justify-between gap-2">
              <p className="flex items-start gap-1.5 text-[12px] text-rejected">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                {job.error}
              </p>
              {job.retryable && job.file ? (
                <Button size="xs" variant="ghost" className="shrink-0" onClick={() => onRetry(job)} aria-label={`Retry ${job.name}`}>
                  <RotateCcw aria-hidden /> Retry
                </Button>
              ) : null}
            </div>
          ) : job.phase === "cancelled" ? (
            <p className="mt-1 flex items-center gap-1.5 text-[12px] text-ink-3">
              <CircleSlash className="size-3.5" aria-hidden />
              Cancelled. Nothing was saved.
            </p>
          ) : (
            <>
              <ol className="mt-2 flex flex-wrap items-center gap-1.5" aria-label="Audit progress">
                {STEPS.map((step, index) => {
                  const done = index < stepIndex || job.phase === "complete";
                  const active = index === stepIndex && running;
                  return (
                    <li key={step.phase} className="flex items-center gap-1.5">
                      {index > 0 ? <span className={cn("h-px w-3", done || active ? "bg-ink-3" : "bg-line-strong")} aria-hidden /> : null}
                      <span
                        className={cn("flex items-center gap-1 text-[11px]", active ? (paced ? "text-review" : "text-accent") : done ? "text-ink-2" : "text-ink-3")}
                      >
                        {active && paced ? (
                          <Hourglass className="size-3" aria-hidden />
                        ) : active ? (
                          <LoaderCircle className="size-3 animate-spin" aria-hidden />
                        ) : done ? (
                          <Check className="size-3" aria-hidden />
                        ) : (
                          <span className="size-1.5 rounded-full bg-line-strong" aria-hidden />
                        )}
                        {step.label}
                      </span>
                    </li>
                  );
                })}
              </ol>
              <div className="relative mt-2 h-[3px] overflow-hidden rounded-full bg-line" role="progressbar" aria-label="Audit progress" aria-valuetext={status}>
                {job.phase === "extracting" ? (
                  // Gemini gives no progress signal: an honest indeterminate bar, not a made-up percentage.
                  <div className={cn("absolute inset-y-0 w-1/3 animate-indeterminate rounded-full", paced ? "bg-review" : "bg-accent")} />
                ) : (
                  <div
                    className={cn(
                      "h-full rounded-full transition-[width] duration-500 ease-out",
                      job.phase === "complete" ? "bg-ink-3" : throttled ? "bg-review" : "bg-accent",
                    )}
                    style={{ width: `${width}%` }}
                  />
                )}
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <p className={cn("text-[12px]", paced ? "text-review" : "text-ink-2")} aria-live="polite">
                  {status}
                  {job.phase === "extracting" && !job.reused && keyWait === 0 ? <span className="figure ml-1.5 text-ink-3">{elapsed}s</span> : null}
                </p>
                {running ? (
                  <Button size="xs" variant="ghost" className="shrink-0" onClick={() => onCancel(job)} aria-label={`Cancel ${job.name}`}>
                    <X aria-hidden /> Cancel
                  </Button>
                ) : job.sha ? (
                  <Tooltip content={<span className="figure break-all">sha256 {job.sha}</span>}>
                    <span className="figure cursor-default text-[11px] text-ink-3" tabIndex={0}>
                      {job.sha.slice(0, 4)}…{job.sha.slice(-4)}
                    </span>
                  </Tooltip>
                ) : null}
              </div>
            </>
          )}

          {job.phase === "complete" && job.result && job.invoiceId ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <StatusBadge status={job.result} openFlags={job.openFlags} />
              {job.replayed ? <span className="text-[11px] text-ink-3">Already audited earlier</span> : null}
              <Button size="xs" variant="ghost" onClick={() => onInspect(job.invoiceId!)}>
                Inspect <ArrowRight aria-hidden />
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function LedgerUploadRow({ invoice, onInspect }: { invoice: InvoiceRecord; onInspect: (id: string) => void }) {
  const now = useNow(60_000);
  return (
    <li className="flex items-start gap-2.5 px-4 py-3">
      <FileIcon type={invoice.document.content_type} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="truncate text-[13px] text-ink" title={invoice.document.filename}>
            {invoice.document.filename}
          </p>
          <span className="shrink-0 text-[11px] text-ink-3">{relativeTime(invoice.created_at, now)}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <StatusBadge status={invoice.status} openFlags={invoice.anomalies.filter((a) => a.status === "OPEN").length} />
          <Button size="xs" variant="ghost" onClick={() => onInspect(invoice.invoice_id)}>
            Inspect <ArrowRight aria-hidden />
          </Button>
        </div>
      </div>
    </li>
  );
}
