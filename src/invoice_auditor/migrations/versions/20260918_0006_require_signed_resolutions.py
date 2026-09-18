"""Every anomaly resolution must be signed in audit_logs.

A deferred constraint trigger refuses to COMMIT any resolution, from any writer, that has no
audit_logs entry written in the same transaction: one for the finding itself, or the invoice's
APPROVED entry (Approve & Archive dismisses every open finding under one signature).

Apply after deploying the release that writes those entries; the previous release resolves
findings without them and would have every review refused. Anything that release recorded after
0005 (invoices ingested, findings resolved) is backfilled first, marked {"backfilled": true}.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Invoices ingested by the previous release since 0005: no INGESTED entry yet.
    op.execute(
        """
        INSERT INTO audit_logs (organization_id, invoice_id, user_id, user_display_name, action,
                                new_status, file_sha256, details, created_at)
        SELECT i.organization_id, i.id, i.uploaded_by, COALESCE(u.full_name, split_part(u.email, '@', 1)), 'INGESTED',
               CASE
                   WHEN i.status = 'PROCESSING' THEN 'PROCESSING'
                   WHEN EXISTS (SELECT 1 FROM anomaly_logs a WHERE a.invoice_id = i.id AND a.severity IN ('HIGH', 'CRITICAL'))
                       THEN 'NEEDS_REVIEW'
                   ELSE 'APPROVED'
               END,
               i.file_hash,
               jsonb_build_object('backfilled', true, 'source', CASE WHEN i.uploaded_by IS NULL THEN 'api' ELSE 'upload' END),
               COALESCE(i.processed_at, i.created_at)
        FROM invoices i
        LEFT JOIN users u ON u.id = i.uploaded_by
        WHERE NOT EXISTS (SELECT 1 FROM audit_logs e WHERE e.invoice_id = i.id AND e.action = 'INGESTED')
        """
    )
    # Findings resolved by the previous release since 0005: neither their own entry nor an
    # APPROVED entry that lists them.
    op.execute(
        """
        INSERT INTO audit_logs (organization_id, invoice_id, anomaly_id, user_id, user_display_name, action,
                                resolution_note, file_sha256, details, created_at)
        SELECT a.organization_id, a.invoice_id, a.id, a.resolved_by, COALESCE(u.full_name, split_part(u.email, '@', 1)),
               CASE a.status WHEN 'DISMISSED' THEN 'ANOMALY_DISMISSED' ELSE 'ANOMALY_CONFIRMED' END,
               a.resolution_note, i.file_hash,
               jsonb_build_object('backfilled', true, 'anomaly_type', a.anomaly_type, 'severity', a.severity),
               a.resolved_at
        FROM anomaly_logs a
        JOIN invoices i ON i.id = a.invoice_id
        JOIN users u ON u.id = a.resolved_by
        WHERE a.status <> 'OPEN'
          AND NOT EXISTS (SELECT 1 FROM audit_logs e WHERE e.anomaly_id = a.id)
          AND NOT EXISTS (
              SELECT 1 FROM audit_logs e
              WHERE e.invoice_id = a.invoice_id AND e.action = 'APPROVED'
                AND e.details -> 'dismissed_anomalies' @> jsonb_build_array(a.id::text)
          )
        """
    )

    # Checked at COMMIT, so the writer may record the decision and its audit entry in either order.
    # now() is the transaction's start time: an entry written in the same transaction carries it.
    op.execute(
        """
        CREATE FUNCTION anomaly_logs_require_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM audit_logs
                WHERE invoice_id = NEW.invoice_id
                  AND created_at = now()
                  AND (anomaly_id = NEW.id OR (action = 'APPROVED' AND anomaly_id IS NULL))
            ) THEN
                RAISE EXCEPTION 'anomaly % was resolved without an audit_logs entry signing the decision', NEW.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_anomaly_logs_require_audit AFTER UPDATE ON anomaly_logs "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (OLD.status = 'OPEN' AND NEW.status <> 'OPEN') "
        "EXECUTE FUNCTION anomaly_logs_require_audit()"
    )


def downgrade() -> None:
    # Backfilled entries stay: the log is append-only, and they are true records.
    op.execute("DROP TRIGGER IF EXISTS trg_anomaly_logs_require_audit ON anomaly_logs")
    op.execute("DROP FUNCTION IF EXISTS anomaly_logs_require_audit()")
