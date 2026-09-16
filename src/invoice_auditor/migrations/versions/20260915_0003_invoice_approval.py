"""Approval stamp on invoices: when an invoice was approved and by whom.

Reviewer approvals record the user; automatic approvals by the rule engine leave approved_by
empty. A trigger stamps approved_at for any writer that sets APPROVED without it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPROVAL_CONSISTENT = (
    "(status = 'APPROVED' AND approved_at IS NOT NULL)"
    " OR (status <> 'APPROVED' AND approved_at IS NULL AND approved_by IS NULL)"
)


def upgrade() -> None:
    op.add_column("invoices", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("approved_by", sa.Uuid()))
    op.create_foreign_key(
        "fk_invoices_approved_by_users", "invoices", "users", ["approved_by"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_invoices_organization_id_approved_at", "invoices", ["organization_id", "approved_at"])

    # Backfill. Invoices that were held for review are attributed to whoever made the last
    # decision on a blocking finding; the rest were approved automatically when processed.
    op.execute(
        """
        UPDATE invoices AS i
        SET approved_at = r.resolved_at, approved_by = r.resolved_by
        FROM (
            SELECT DISTINCT ON (invoice_id) invoice_id, resolved_at, resolved_by
            FROM anomaly_logs
            WHERE status <> 'OPEN' AND severity IN ('HIGH', 'CRITICAL')
            ORDER BY invoice_id, resolved_at DESC
        ) AS r
        WHERE i.status = 'APPROVED' AND r.invoice_id = i.id
        """
    )
    op.execute("UPDATE invoices SET approved_at = COALESCE(processed_at, created_at) WHERE status = 'APPROVED' AND approved_at IS NULL")

    op.execute(
        """
        CREATE FUNCTION invoices_stamp_approval() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status = 'APPROVED' AND NEW.approved_at IS NULL THEN
                NEW.approved_at := now();
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_invoices_stamp_approval BEFORE INSERT OR UPDATE ON invoices "
        "FOR EACH ROW EXECUTE FUNCTION invoices_stamp_approval()"
    )
    op.create_check_constraint("ck_invoices_approval_consistent", "invoices", APPROVAL_CONSISTENT)


def downgrade() -> None:
    op.drop_constraint("ck_invoices_approval_consistent", "invoices", type_="check")
    op.execute("DROP TRIGGER IF EXISTS trg_invoices_stamp_approval ON invoices")
    op.execute("DROP FUNCTION IF EXISTS invoices_stamp_approval()")
    op.drop_index("ix_invoices_organization_id_approved_at", table_name="invoices")
    op.drop_constraint("fk_invoices_approved_by_users", "invoices", type_="foreignkey")
    op.drop_column("invoices", "approved_by")
    op.drop_column("invoices", "approved_at")
