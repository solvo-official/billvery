"""Immutable audit trail (audit_logs) and tamper-proof stored originals.

Every invoice gets a chain of custody: an INGESTED entry carrying the SHA-256 of its document, then
one entry per reviewer decision naming the signer, the note, and the status before and after.

The database protects it on its own, whatever the writer:
- audit_logs is append-only (updates always refused; deletes only when the whole tenant goes);
- stored originals in `documents` can't be rewritten, so the recorded hash stays verifiable.

Existing data is backfilled: an INGESTED entry per invoice and one entry per resolved finding,
marked {"backfilled": true}. Statuses a backfill can't know are left empty.

Safe to apply while the previous release is still serving: it doesn't change anything that release
writes. Requiring a signed entry for every resolution comes in 0006, applied once the release that
writes the entries is deployed.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the enum values: migrations must not change when application code does.
INVOICE_STATUSES = ("PROCESSING", "NEEDS_REVIEW", "APPROVED", "REJECTED")
AUDIT_ACTIONS = ("INGESTED", "ANOMALY_DISMISSED", "ANOMALY_CONFIRMED", "APPROVED")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("anomaly_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("user_display_name", sa.String(255)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("previous_status", sa.String(20)),
        sa.Column("new_status", sa.String(20)),
        sa.Column("file_sha256", sa.CHAR(64), nullable=False),
        sa.Column("details", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_audit_logs_organization_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="RESTRICT",
            name="fk_audit_logs_invoice_id_organization_id_invoices",
        ),
        sa.ForeignKeyConstraint(["anomaly_id"], ["anomaly_logs.id"], ondelete="RESTRICT", name="fk_audit_logs_anomaly_id_anomaly_logs"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_audit_logs_user_id_users"),
        sa.CheckConstraint(_one_of("action", AUDIT_ACTIONS), name="ck_audit_logs_action_valid"),
        sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="ck_audit_logs_file_sha256_hex"),
        sa.CheckConstraint(
            "previous_status IS NULL OR " + _one_of("previous_status", INVOICE_STATUSES), name="ck_audit_logs_previous_status_valid"
        ),
        sa.CheckConstraint("new_status IS NULL OR " + _one_of("new_status", INVOICE_STATUSES), name="ck_audit_logs_new_status_valid"),
        sa.CheckConstraint(
            "action = 'INGESTED' OR (user_id IS NOT NULL AND user_display_name IS NOT NULL)", name="ck_audit_logs_review_signed"
        ),
        sa.CheckConstraint(
            "(action IN ('ANOMALY_DISMISSED', 'ANOMALY_CONFIRMED')) = (anomaly_id IS NOT NULL)",
            name="ck_audit_logs_anomaly_reference",
        ),
    )
    op.create_index("ix_audit_logs_invoice_id_created_at", "audit_logs", ["invoice_id", "created_at"])
    op.create_index("ix_audit_logs_anomaly_id", "audit_logs", ["anomaly_id"])
    op.create_index("ix_audit_logs_organization_id_created_at", "audit_logs", ["organization_id", "created_at"])

    # --- backfill ---------------------------------------------------------------------------
    # Ingestion: the status an invoice got from the rule engine follows from its findings, which
    # are all written at ingestion (any HIGH/CRITICAL one held it for review).
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
        """
    )
    # Past decisions: signer, note and time are on the finding; the invoice's status at that
    # moment isn't recorded anywhere, so previous_status and new_status stay empty.
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
        """
    )

    # --- append-only ------------------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION audit_logs_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit log entries are immutable (entry %)', OLD.id USING ERRCODE = 'check_violation';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_logs_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- The only legitimate delete is the cascade from removing the whole tenant.
            IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN
                RAISE EXCEPTION 'audit logs are append-only (entry %)', OLD.id USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN OLD;
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER trg_audit_logs_guard_update BEFORE UPDATE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_guard_update()")
    op.execute("CREATE TRIGGER trg_audit_logs_guard_delete BEFORE DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_logs_guard_delete()")

    # --- stored originals can't be rewritten ------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION documents_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'stored documents are immutable (%)', OLD.sha256 USING ERRCODE = 'check_violation';
        END
        $$
        """
    )
    op.execute("CREATE TRIGGER trg_documents_guard_update BEFORE UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION documents_guard_update()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_documents_guard_update ON documents")
    op.execute("DROP FUNCTION IF EXISTS documents_guard_update()")
    op.drop_table("audit_logs")  # drops its own guard triggers
    op.execute("DROP FUNCTION IF EXISTS audit_logs_guard_delete()")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_guard_update()")
