import { useCallback, useState } from "react";
import { ReviewQueue } from "@/components/feed/ReviewQueue";
import { InvoiceTable } from "@/components/feed/InvoiceTable";
import { IngestionDropzone } from "@/components/ingestion/IngestionDropzone";
import { AnomalyInspectionDrawer } from "@/components/inspection/AnomalyInspectionDrawer";
import { PageHeader } from "@/components/shell/PageHeader";
import { StatsRibbon } from "@/components/stats/StatsRibbon";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useOrganization } from "@/state/audit-store";
import { ApproveDialog } from "./ApproveDialog";

/** The review workspace: metrics, the audit feed, ingestion and the queue, plus the inspection drawer. */
export function ReviewView() {
  const organization = useOrganization();
  const [inspecting, setInspecting] = useState<string | null>(null);
  const [approving, setApproving] = useState<string | null>(null);
  const closeDrawer = useCallback(() => setInspecting(null), []);
  const closeDialog = useCallback(() => setApproving(null), []);

  return (
    <>
      <main className="mx-auto flex max-w-[1600px] animate-fade-in flex-col gap-5 px-4 pb-10 pt-6 sm:px-6">
        <PageHeader
          title="Accounts payable audit"
          description={
            <>
              {organization.legal_name ?? organization.name} · reporting in <span className="figure text-ink-2">{organization.currency_code}</span>
            </>
          }
        />
        <ErrorBoundary label="Audit metrics">
          <StatsRibbon />
        </ErrorBoundary>
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_344px]">
          <ErrorBoundary label="The audit feed">
            <InvoiceTable selectedId={inspecting} onOpen={setInspecting} onApprove={setApproving} />
          </ErrorBoundary>
          <aside aria-label="Ingestion and review queue" className="order-first flex flex-col gap-5 xl:order-none">
            <ErrorBoundary label="Document ingestion">
              <IngestionDropzone onInspect={setInspecting} />
            </ErrorBoundary>
            <ErrorBoundary label="The review queue">
              <ReviewQueue onOpen={setInspecting} />
            </ErrorBoundary>
          </aside>
        </div>
      </main>
      <AnomalyInspectionDrawer invoiceId={inspecting} onClose={closeDrawer} onNavigate={setInspecting} onApprove={setApproving} />
      <ApproveDialog invoiceId={approving} onClose={closeDialog} />
    </>
  );
}
