import { PlugZap, RotateCw } from "lucide-react";
import { lazy, Suspense } from "react";
import { Toaster } from "sonner";
import { ApprovedBillsView } from "@/components/approved/ApprovedBillsView";
import { ReviewView } from "@/components/review/ReviewView";
import { DashboardSkeleton, StatsSkeleton, TableSkeleton } from "@/components/shell/DashboardSkeleton";
import { PageHeaderSkeleton } from "@/components/shell/PageHeader";
import { SignIn } from "@/components/shell/SignIn";
import { TopBar } from "@/components/shell/TopBar";
import { Button } from "@/components/ui/button";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { LoadingLabel } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useRoute } from "@/hooks/use-route";
import { ThemeProvider, useTheme } from "@/hooks/use-theme";
import type { ApiError } from "@/lib/api";
import { AuditStoreProvider, useAudit } from "@/state/audit-store";

// Secondary workspaces load on first visit, keeping the review console's bundle small.
const TeamView = lazy(() => import("@/components/team/TeamView").then((module) => ({ default: module.TeamView })));
const VendorsView = lazy(() => import("@/components/vendors/VendorsView").then((module) => ({ default: module.VendorsView })));

export default function App() {
  return (
    <ThemeProvider>
      <AuditStoreProvider>
        <TooltipProvider delayDuration={250} skipDelayDuration={150}>
          <Console />
          <ThemedToaster />
        </TooltipProvider>
      </AuditStoreProvider>
    </ThemeProvider>
  );
}

function ThemedToaster() {
  const { theme } = useTheme();
  return (
    <Toaster
      theme={theme}
      position="bottom-right"
      gap={10}
      offset={20}
      toastOptions={{
        classNames: {
          toast: "!rounded-xl !border !border-line !bg-raised !font-sans !text-[13px] !text-ink !shadow-overlay",
          title: "!font-medium !text-ink",
          description: "!text-[12px] !text-ink-2",
          actionButton: "!rounded-md !bg-accent !text-on-accent !font-medium",
          icon: "!text-ink-2",
          success: "[&_[data-icon]]:!text-approved",
          error: "[&_[data-icon]]:!text-rejected",
          info: "[&_[data-icon]]:!text-accent",
        },
      }}
    />
  );
}

/** Nothing renders against the ledger until the API has answered; skeletons hold the layout meanwhile. */
function Console() {
  const { connection } = useAudit();
  const { view } = useRoute();
  return (
    <div className="relative isolate min-h-dvh bg-ground">
      <div className="app-backdrop pointer-events-none fixed inset-x-0 top-0 -z-10 h-[560px]" aria-hidden />
      <TopBar />
      <ErrorBoundary label="This workspace" resetKeys={[view, connection.status]} className="m-4 sm:m-6">
        {connection.status === "ready" ? (
          <Suspense fallback={<ArchiveSkeleton label="Loading…" />}>
            {view === "approved" ? <ApprovedBillsView /> : view === "vendors" ? <VendorsView /> : view === "team" ? <TeamView /> : <ReviewView />}
          </Suspense>
        ) : connection.status === "connecting" ? (
          view === "review" ? <DashboardSkeleton /> : <ArchiveSkeleton />
        ) : connection.status === "signed-out" ? (
          <SignIn googleLogin={connection.googleLogin} />
        ) : (
          <ConnectionFailed error={connection.error} />
        )}
      </ErrorBoundary>
    </div>
  );
}

function ArchiveSkeleton({ label = "Loading approved bills…" }: { label?: string }) {
  return (
    <main className="mx-auto flex max-w-[1600px] flex-col gap-5 px-4 pb-10 pt-6 sm:px-6">
      <LoadingLabel>{label}</LoadingLabel>
      <PageHeaderSkeleton />
      <StatsSkeleton compact />
      <TableSkeleton rows={8} />
    </main>
  );
}

function explain(error: ApiError): { title: string; hint: string } {
  if (error.unreachable) {
    return {
      title: "Can't reach the audit API",
      hint: "Start the backend (uvicorn invoice_auditor.main:create_app --factory --port 8000) and check AUDITOR_API_URL in frontend/.env.local.",
    };
  }
  if (error.status === 401) {
    return {
      title: "The API key was rejected",
      hint: "Set AUDITOR_API_KEY in frontend/.env.local to a valid, unrevoked key, then restart the dev server.",
    };
  }
  if (error.status === 403) {
    return { title: "This API key can't read invoices", hint: "Issue a key with the invoices:read scope (invoice-auditor create-api-key)." };
  }
  return {
    title: "The audit API returned an error",
    hint: "If the engine status at the top right says the database is unreachable, start PostgreSQL and try again.",
  };
}

function ConnectionFailed({ error }: { error: ApiError }) {
  const { reconnect } = useAudit();
  const { title, hint } = explain(error);
  return (
    <main className="mx-auto flex max-w-[1600px] justify-center px-4 py-20 sm:px-6">
      <section role="alert" className="glass w-full max-w-lg animate-rise-in rounded-2xl px-6 py-6">
        <div className="flex items-start gap-3.5">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-rejected/10 text-rejected ring-1 ring-inset ring-rejected/20" aria-hidden>
            <PlugZap className="size-5" strokeWidth={1.75} />
          </span>
          <div className="min-w-0">
            <h1 className="text-[15px] font-semibold text-ink">{title}</h1>
            <p className="mt-1 text-[13px] text-ink-2">{error.message}</p>
            <p className="mt-3 text-[12px] leading-relaxed text-ink-3">{hint}</p>
            <Button className="mt-4" variant="secondary" onClick={reconnect}>
              <RotateCw aria-hidden /> Try again
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
