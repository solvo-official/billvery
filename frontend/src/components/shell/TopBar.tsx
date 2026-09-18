import { Archive, Check, ChevronDown, Copy, Inbox, LoaderCircle, LogOut, Monitor, Moon, Plus, Settings, Store, Sun, UserCheck, Users, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { useEffect, useState } from "react";
import { Avatar } from "@/components/audit/status";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { useNow } from "@/hooks/use-now";
import { useRoute, type View } from "@/hooks/use-route";
import { useTheme, type ThemePreference } from "@/hooks/use-theme";
import { api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Health, Workspace } from "@/lib/types";
import { useAudit } from "@/state/audit-store";

export function TopBar() {
  const { organization, connection } = useAudit();
  // Placeholders only while connecting; a failed connection shows nothing rather than a shimmer.
  const connecting = connection.status === "connecting";
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-ground/75 backdrop-blur-xl backdrop-saturate-150">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-2 px-4 sm:gap-3 sm:px-6">
        <Brand />
        <span className="hidden h-5 w-px rotate-12 bg-line-strong sm:block" aria-hidden />
        {organization ? <OrganizationMenu /> : connecting ? <Skeleton className="h-4 w-28" /> : null}
        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <EngineStatus />
          <ThemeMenu />
          {organization ? <AccountMenu /> : connecting ? <Skeleton className="size-8 rounded-full" /> : null}
        </div>
      </div>
      <PrimaryNav />
    </header>
  );
}

/** The auditor's tick-and-tie mark: a tick with a crossbar. */
function Brand() {
  return (
    <a href="#/review" className="flex items-center gap-2 rounded-md" aria-label="Billvery, review queue">
      <span className="flex size-7 items-center justify-center rounded-lg bg-accent text-on-accent shadow-[var(--shadow-card),inset_0_1px_0_rgb(255_255_255/0.2)]">
        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M4.5 12.5 9 17 19.5 6.5" />
          <path d="M12.5 8.5 16.5 12.5" />
        </svg>
      </span>
      <span className="hidden text-[14px] font-semibold tracking-[-0.01em] text-ink sm:inline">Billvery</span>
    </a>
  );
}

const NAV: { view: View; label: string; icon: LucideIcon }[] = [
  { view: "review", label: "Review", icon: Inbox },
  { view: "approved", label: "Approved Bills", icon: Archive },
  { view: "vendors", label: "Vendors", icon: Store },
  { view: "team", label: "Team", icon: Users },
];

/** Workspace tabs, underlined like a document's section tabs. Counts update with every approval. */
function PrimaryNav() {
  const { view, href } = useRoute();
  const { connection, invoices, archive, users, vendors } = useAudit();
  const ready = connection.status === "ready";
  let review = 0;
  let approved = 0;
  for (const invoice of invoices) {
    if (invoice.status === "NEEDS_REVIEW") review += 1;
    else if (invoice.status === "APPROVED") approved += 1;
  }
  const failed = connection.status === "error";
  const highRisk = vendors.list.filter((card) => card.risk.tier === "HIGH").length;
  const counts: Record<View, number | null> = {
    review: ready ? review : null,
    approved: ready && (archive.status === "ready" || archive.status === "error") ? approved : null,
    // High-risk vendors when there are any, so the tab says whether it needs a look.
    vendors: ready && vendors.status !== "idle" ? (highRisk || vendors.list.length) : null,
    team: ready ? users.length : null,
  };

  return (
    <nav aria-label="Workspaces" className="mx-auto flex h-10 max-w-[1600px] items-stretch gap-1 overflow-x-auto px-2.5 sm:px-4.5">
      {NAV.map(({ view: target, label, icon: Icon }) => {
        const active = view === target;
        const count = counts[target];
        return (
          <a
            key={target}
            href={href(target)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "group relative flex shrink-0 items-center gap-2 px-2.5 text-[13px] font-medium transition-colors",
              active ? "text-ink" : "text-ink-3 hover:text-ink-2",
            )}
          >
            <span className="flex items-center gap-2 rounded-md px-1 py-1 transition-colors group-hover:bg-hover">
              <Icon className={cn("size-4", active ? "text-ink-2" : "text-ink-3")} aria-hidden />
              {label}
              {count === null ? (
                failed ? null : <Skeleton className="h-[18px] w-6 rounded-full" />
              ) : (
                <span
                  key={count}
                  className={cn(
                    "figure inline-flex h-[18px] min-w-6 animate-pop-in items-center justify-center rounded-full px-1.5 text-[11px]",
                    target === "review" && count > 0
                      ? "bg-review/15 text-review"
                      : target === "approved"
                        ? "bg-approved/12 text-approved"
                        : target === "vendors" && highRisk > 0
                          ? "bg-rejected/12 text-rejected"
                          : "bg-ink-3/10 text-ink-2",
                  )}
                  aria-label={`${count} ${
                    target === "review"
                      ? "waiting for review"
                      : target === "approved"
                        ? "approved"
                        : target === "vendors"
                          ? highRisk > 0
                            ? "high-risk vendors"
                            : "vendors"
                          : "with access"
                  }`}
                >
                  {count.toLocaleString()}
                </span>
              )}
            </span>
            <span
              className={cn(
                "absolute inset-x-2.5 -bottom-px h-[2px] rounded-full bg-ink transition-[opacity,transform] duration-200",
                active ? "scale-x-100 opacity-100" : "scale-x-50 opacity-0",
              )}
              aria-hidden
            />
          </a>
        );
      })}
    </nav>
  );
}

/** The current workspace, and every other workspace this Google account can switch to. */
function OrganizationMenu() {
  const { organization, users, session } = useAudit();
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  if (!organization) return null;
  const signedIn = Boolean(session?.user);

  const load = (open: boolean) => {
    if (!open || !signedIn) return;
    setFailed(false);
    api.workspaces().then(setWorkspaces, () => setFailed(true));
  };

  const switchTo = async (workspace: Workspace) => {
    if (workspace.current || busy) return;
    setBusy(workspace.organization.id);
    try {
      await api.switchWorkspace(workspace.organization.id);
      window.location.reload();
    } catch (error) {
      setBusy(null);
      toast.error(`Couldn't open ${workspace.organization.name}`, { description: toApiError(error).message });
    }
  };

  const create = async () => {
    if (busy) return;
    setBusy("new");
    try {
      await api.createWorkspace();
      window.location.hash = "#/team";
      window.location.reload();
    } catch (error) {
      setBusy(null);
      toast.error("Couldn't create a workspace", { description: toApiError(error).message });
    }
  };

  return (
    <DropdownMenu onOpenChange={load}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 min-w-0 items-center gap-2 rounded-md px-2 text-left transition-colors hover:bg-hover data-[state=open]:bg-hover"
          aria-label={`Workspace: ${organization.name}`}
        >
          {organization.country_code ? <CountryChip code={organization.country_code} /> : null}
          <span className="truncate text-[13px] font-medium">{organization.name}</span>
          <ChevronDown className="size-3.5 shrink-0 text-ink-3" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-80">
        <div className="px-2 py-2">
          <p className="font-medium">{organization.legal_name ?? organization.name}</p>
          <p className="mt-0.5 text-[12px] text-ink-3">
            {organization.currency_code} · {users.length} member{users.length === 1 ? "" : "s"} · invoices are private to this workspace
          </p>
        </div>
        {signedIn ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Your workspaces</DropdownMenuLabel>
            {workspaces === null && !failed ? (
              <div className="flex items-center gap-2 px-2 py-2 text-[12px] text-ink-3">
                <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> Loading…
              </div>
            ) : failed ? (
              <p className="px-2 py-2 text-[12px] text-rejected">Couldn't load your workspaces. Close and reopen this menu to retry.</p>
            ) : (
              workspaces!.map((workspace) => (
                <DropdownMenuItem
                  key={workspace.organization.id}
                  onSelect={(event) => {
                    event.preventDefault();
                    void switchTo(workspace);
                  }}
                  className="py-2"
                >
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-accent/10 font-label text-[11px] font-semibold text-accent">
                    {workspace.organization.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{workspace.organization.name}</span>
                    <span className="block truncate text-[11px] capitalize text-ink-3">{workspace.role ?? "API key"}</span>
                  </span>
                  {busy === workspace.organization.id ? (
                    <LoaderCircle className="animate-spin text-ink-3" aria-label="Switching" />
                  ) : workspace.current ? (
                    <Check className="text-accent" aria-label="Current workspace" />
                  ) : null}
                </DropdownMenuItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault();
                void create();
              }}
            >
              {busy === "new" ? <LoaderCircle className="animate-spin text-ink-3" aria-hidden /> : <Plus className="text-ink-3" aria-hidden />}
              Create a new workspace
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href="#/team">
                <Settings className="text-ink-3" aria-hidden /> Team and workspace settings
              </a>
            </DropdownMenuItem>
          </>
        ) : (
          <>
            <DropdownMenuSeparator />
            <p className="px-2 py-1.5 text-[11px] leading-snug text-ink-3">
              Using the development API key. Sign in with Google to switch or create workspaces.
            </p>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CountryChip({ code, className }: { code: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 w-7 shrink-0 items-center justify-center rounded-[4px] border border-line bg-sunken font-mono text-[10px] text-ink-2",
        className,
      )}
    >
      {code}
    </span>
  );
}

interface HealthCheck {
  health: Health | null;
  latencyMs: number | null;
  error: ApiError | null;
  checkedAt: number;
}

function EngineStatus() {
  const [check, setCheck] = useState<HealthCheck | null>(null);
  const now = useNow(5_000);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const { health, latencyMs } = await api.health();
        if (active) setCheck({ health, latencyMs, error: null, checkedAt: Date.now() });
      } catch (error) {
        if (active) setCheck({ health: null, latencyMs: null, error: toApiError(error), checkedAt: Date.now() });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const state = !check ? "checking" : check.error ? "offline" : check.health?.extraction.configured ? "healthy" : "degraded";
  const label = { checking: "Checking", healthy: "Healthy", degraded: "Degraded", offline: "Offline" }[state];
  const dot = {
    checking: "bg-ink-3",
    healthy: "animate-beacon bg-approved text-approved",
    degraded: "bg-review text-review",
    offline: "bg-rejected text-rejected",
  }[state];

  const details = !check ? (
    "Checking the audit engine…"
  ) : check.error ? (
    <span>{check.error.message}</span>
  ) : (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-ink-3">Database</dt>
      <dd>Connected</dd>
      <dt className="text-ink-3">Extraction</dt>
      <dd className="figure">
        {check.health?.extraction.configured
          ? `${check.health.extraction.model}${(check.health.extraction.api_keys ?? 0) > 1 ? ` · ${check.health.extraction.api_keys} API keys` : ""}`
          : "Not configured: uploads are off"}
      </dd>
      <dt className="text-ink-3">API latency</dt>
      <dd className="figure">{check.latencyMs} ms</dd>
      <dt className="text-ink-3">Version</dt>
      <dd className="figure">{check.health?.version}</dd>
      <dt className="text-ink-3">Last check</dt>
      <dd>{Math.max(0, Math.round((now - check.checkedAt) / 1000))} s ago</dd>
    </dl>
  );

  return (
    <Tooltip content={details} align="end">
      <button
        type="button"
        className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface-solid px-2.5 text-[12px] shadow-[var(--shadow-card)] transition-colors hover:border-line-strong"
      >
        <span className={cn("size-2 rounded-full", dot)} aria-hidden />
        <span className="hidden text-ink-3 sm:inline">Engine</span>
        <span className="text-ink">{label}</span>
        {check?.latencyMs != null ? <span className="figure hidden text-ink-3 lg:inline">{check.latencyMs} ms</span> : null}
      </button>
    </Tooltip>
  );
}

const THEMES: { value: ThemePreference; label: string; icon: LucideIcon }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

function ThemeMenu() {
  const { preference, theme, setPreference } = useTheme();
  const Icon = theme === "dark" ? Moon : Sun;
  return (
    <DropdownMenu>
      <Tooltip content="Theme">
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex size-8 items-center justify-center rounded-full border border-line bg-surface-solid text-ink-2 shadow-[var(--shadow-card)] transition-colors hover:border-line-strong hover:text-ink data-[state=open]:text-ink"
            aria-label={`Theme: ${preference}`}
          >
            <Icon key={theme} className="size-4 animate-pop-in" aria-hidden />
          </button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={preference} onValueChange={(value) => setPreference(value as ThemePreference)}>
          {THEMES.map(({ value, label, icon: ItemIcon }) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <ItemIcon className="text-ink-3" aria-hidden />
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Signed in with Google: the account it belongs to. Otherwise the local "acting as" picker. */
function AccountMenu() {
  const { session } = useAudit();
  return session?.user ? <SignedInMenu /> : <ReviewerMenu />;
}

function SignedInMenu() {
  const { session, signOut } = useAudit();
  const user = session!.user!;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface-solid pl-1 pr-1 shadow-[var(--shadow-card)] transition-colors hover:border-line-strong data-[state=open]:border-line-strong sm:pr-2.5"
          aria-label={`Signed in as ${user.name}`}
        >
          <Avatar name={user.name} />
          <span className="hidden text-[12px] font-medium sm:inline">{user.name}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <div className="flex items-center gap-2.5 px-2 py-2">
          <Avatar name={user.name} className="size-8 text-[12px]" />
          <span className="min-w-0">
            <span className="block truncate font-medium">{user.name}</span>
            <span className="block truncate text-[11px] text-ink-3">{user.email}</span>
          </span>
        </div>
        <p className="px-2 pb-1.5 text-[11px] text-ink-3">
          Signed in with Google ·{" "}
          <span className="rounded-full bg-hover px-1.5 font-label text-[10px] font-semibold uppercase tracking-[0.06em] text-ink-2">{user.role}</span>
        </p>
        <DropdownMenuSeparator />
        <p className="flex items-start gap-1.5 px-2 pb-1.5 pt-0.5 text-[11px] text-ink-3">
          <UserCheck className="mt-px size-3 shrink-0" aria-hidden />
          {user.can_resolve
            ? "Uploads, resolutions and approvals are recorded in your name."
            : "Your role can view invoices, but not approve them."}
        </p>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOut className="text-ink-3" aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ReviewerMenu() {
  const { reviewer, reviewers, setReviewer } = useAudit();
  const [copied, setCopied] = useState(false);

  return (
    <DropdownMenu onOpenChange={() => setCopied(false)}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface-solid pl-1 pr-1 shadow-[var(--shadow-card)] transition-colors hover:border-line-strong data-[state=open]:border-line-strong sm:pr-2.5"
          aria-label={reviewer ? `Acting as ${reviewer.name}` : "No reviewer selected"}
        >
          {reviewer ? <Avatar name={reviewer.name} /> : <span className="flex size-6 items-center justify-center rounded-full bg-hover text-ink-3">?</span>}
          <span className="hidden text-[12px] font-medium sm:inline">{reviewer?.name ?? "No reviewer"}</span>
          {reviewer ? (
            <span className="hidden rounded-full bg-hover px-1.5 font-label text-[10px] font-semibold uppercase tracking-[0.06em] text-ink-2 md:inline">
              {reviewer.role}
            </span>
          ) : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel>Acting as</DropdownMenuLabel>
        {reviewers.length === 0 ? (
          <p className="px-2 pb-2 text-[12px] text-ink-3">
            No one in this organization can resolve anomalies yet. Add a reviewer with{" "}
            <span className="figure text-ink-2">invoice-auditor create-user --role reviewer</span>.
          </p>
        ) : (
          reviewers.map((user) => (
            <DropdownMenuItem key={user.id} onSelect={() => setReviewer(user.id)} className="py-2">
              <Avatar name={user.name} />
              <span className="min-w-0 flex-1">
                <span className="block truncate">{user.name}</span>
                <span className="block truncate text-[11px] text-ink-3">
                  {user.email} · {user.role}
                </span>
              </span>
              {user.id === reviewer?.id ? <Check className="!size-4 text-accent" aria-label="Selected" /> : null}
            </DropdownMenuItem>
          ))
        )}
        {reviewer ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Reviewer ID</DropdownMenuLabel>
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault();
                navigator.clipboard?.writeText(reviewer.id).then(() => setCopied(true), () => undefined);
              }}
            >
              <span className="figure flex-1 truncate text-[12px] text-ink-2">{reviewer.id}</span>
              {copied ? <Check className="text-approved" aria-label="Copied" /> : <Copy className="text-ink-3" aria-label="Copy" />}
            </DropdownMenuItem>
            <p className="flex items-start gap-1.5 px-2 pb-1.5 pt-0.5 text-[11px] text-ink-3">
              <UserCheck className="mt-px size-3 shrink-0" aria-hidden />
              Uploads, resolutions and approvals are recorded against this person.
            </p>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
