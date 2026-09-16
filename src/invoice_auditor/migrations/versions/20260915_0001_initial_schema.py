"""Initial schema: tenants, vendors, invoices, line items, immutable anomaly logs.

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the enum values: migrations must not change when application code does.
INVOICE_STATUSES = ("PROCESSING", "NEEDS_REVIEW", "APPROVED", "REJECTED")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
ANOMALY_STATUSES = ("OPEN", "DISMISSED", "CONFIRMED")
USER_ROLES = ("owner", "admin", "reviewer", "member")

MONEY = sa.Numeric(18, 4)
CONFIDENCE = sa.Numeric(5, 2)
TIMESTAMPTZ = sa.DateTime(timezone=True)
EMPTY_JSON = sa.text("'{}'::jsonb")
UPDATED_AT_TABLES = ("organizations", "users", "vendors", "tax_rate_limits", "invoices", "invoice_items")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column("created_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "organizations",
        _id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(255)),
        sa.Column("industry", sa.String(100)),
        sa.Column("country_code", sa.CHAR(2)),
        sa.Column("currency_code", sa.CHAR(3), server_default="USD", nullable=False),
        sa.Column("subscription_plan", sa.String(50), server_default="starter", nullable=False),
        sa.Column("settings", JSONB, server_default=EMPTY_JSON, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_organizations_country_code_iso"),
        sa.CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="ck_organizations_currency_code_iso"),
    )
    op.create_index("ix_organizations_name", "organizations", ["name"])
    op.create_index("ix_organizations_is_active", "organizations", ["is_active"])

    op.create_table(
        "users",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("full_name", sa.String(150)),
        sa.Column("role", sa.String(20), server_default="member", nullable=False),
        sa.Column("permissions", JSONB, server_default=EMPTY_JSON, nullable=False),
        sa.Column("last_login_at", TIMESTAMPTZ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_users_organization_id_organizations"
        ),
        sa.UniqueConstraint("organization_id", "email", name="uq_users_organization_id_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        sa.CheckConstraint(_one_of("role", USER_ROLES), name="ck_users_role_valid"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "api_keys",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.CHAR(64), nullable=False),
        sa.Column("permissions", JSONB, server_default=EMPTY_JSON, nullable=False),
        sa.Column("last_used_at", TIMESTAMPTZ),
        sa.Column("expires_at", TIMESTAMPTZ),
        sa.Column("revoked_at", TIMESTAMPTZ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_api_keys_organization_id_organizations"
        ),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_api_keys_key_hash_sha256"),
    )
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])

    op.create_table(
        "vendors",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("tax_id", sa.String(100)),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_vendors"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_vendors_organization_id_organizations"
        ),
        sa.UniqueConstraint("organization_id", "normalized_name", name="uq_vendors_organization_id_normalized_name"),
        sa.UniqueConstraint("id", "organization_id", name="uq_vendors_id_organization_id"),
        sa.CheckConstraint("normalized_name <> ''", name="ck_vendors_normalized_name_not_empty"),
    )
    op.create_index(
        "uq_vendors_organization_id_tax_id",
        "vendors",
        ["organization_id", "tax_id"],
        unique=True,
        postgresql_where=sa.text("tax_id IS NOT NULL"),
    )
    op.create_index(
        "ix_vendors_normalized_name_trgm",
        "vendors",
        ["normalized_name"],
        postgresql_using="gin",
        postgresql_ops={"normalized_name": "gin_trgm_ops"},
    )

    op.create_table(
        "tax_rate_limits",
        sa.Column("country_code", sa.CHAR(2), nullable=False),
        sa.Column("max_rate_percent", MONEY, nullable=False),
        sa.Column("note", sa.Text()),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("country_code", name="pk_tax_rate_limits"),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_tax_rate_limits_country_code_iso"),
        sa.CheckConstraint("max_rate_percent >= 0 AND max_rate_percent <= 100", name="ck_tax_rate_limits_max_rate_range"),
    )

    op.create_table(
        "invoices",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_id", sa.Uuid()),
        sa.Column("uploaded_by", sa.Uuid()),
        sa.Column("vendor_name", sa.String(255)),
        sa.Column("vendor_tax_id", sa.String(100)),
        sa.Column("invoice_number", sa.String(100)),
        sa.Column("invoice_number_normalized", sa.String(100)),
        sa.Column("invoice_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("subtotal", MONEY),
        sa.Column("tax_amount", MONEY, server_default="0", nullable=False),
        sa.Column("shipping_amount", MONEY, server_default="0", nullable=False),
        sa.Column("discount_amount", MONEY, server_default="0", nullable=False),
        sa.Column("total_amount", MONEY),
        sa.Column("status", sa.String(20), server_default="PROCESSING", nullable=False),
        sa.Column("ai_confidence", CONFIDENCE),
        sa.Column("ai_extraction", JSONB, server_default=EMPTY_JSON, nullable=False),
        sa.Column("source_file_url", sa.Text()),
        sa.Column("source_file_name", sa.String(255), nullable=False),
        sa.Column("file_mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_hash", sa.CHAR(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("metadata", JSONB, server_default=EMPTY_JSON, nullable=False),
        sa.Column("processed_at", TIMESTAMPTZ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE", name="fk_invoices_organization_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id", "organization_id"],
            ["vendors.id", "vendors.organization_id"],
            ondelete="RESTRICT",
            name="fk_invoices_vendor_id_organization_id_vendors",
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL", name="fk_invoices_uploaded_by_users"),
        sa.UniqueConstraint("id", "organization_id", name="uq_invoices_id_organization_id"),
        sa.CheckConstraint(_one_of("status", INVOICE_STATUSES), name="ck_invoices_status_valid"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_invoices_currency_iso"),
        sa.CheckConstraint("file_hash ~ '^[0-9a-f]{64}$'", name="ck_invoices_file_hash_sha256"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_invoices_file_size_positive"),
        sa.CheckConstraint("ai_confidence BETWEEN 0 AND 100", name="ck_invoices_ai_confidence_range"),
    )
    op.create_index("ix_invoices_organization_id_file_hash", "invoices", ["organization_id", "file_hash"])
    op.create_index(
        "ix_invoices_duplicate_lookup",
        "invoices",
        ["organization_id", "vendor_id", "invoice_number_normalized", "created_at"],
    )
    op.create_index(
        "ix_invoices_organization_id_status_created_at", "invoices", ["organization_id", "status", "created_at"]
    )
    op.create_index("ix_invoices_organization_id_invoice_date", "invoices", ["organization_id", "invoice_date"])
    op.create_index(
        "uq_invoices_organization_id_idempotency_key",
        "invoices",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "invoice_items",
        _id(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("description_normalized", sa.String(500)),
        sa.Column("quantity", MONEY, server_default="1", nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("line_total", MONEY, nullable=False),
        sa.Column("extracted_data", JSONB, server_default=EMPTY_JSON, nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_items"),
        sa.ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="CASCADE",
            name="fk_invoice_items_invoice_id_organization_id_invoices",
        ),
        sa.UniqueConstraint("invoice_id", "line_number", name="uq_invoice_items_invoice_id_line_number"),
        sa.CheckConstraint("line_number > 0", name="ck_invoice_items_line_number_positive"),
    )
    op.create_index(
        "ix_invoice_items_price_history", "invoice_items", ["organization_id", "description_normalized"]
    )
    op.execute(
        "CREATE INDEX ix_invoice_items_description_fts ON invoice_items "
        "USING gin (to_tsvector('english', coalesce(description, '')))"
    )

    op.create_table(
        "anomaly_logs",
        _id(),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("anomaly_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("confidence_score", CONFIDENCE, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detected_value", JSONB),
        sa.Column("expected_value", JSONB),
        sa.Column("status", sa.String(12), server_default="OPEN", nullable=False),
        sa.Column("resolved_by", sa.Uuid()),
        sa.Column("resolved_at", TIMESTAMPTZ),
        sa.Column("resolution_note", sa.Text()),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_anomaly_logs"),
        sa.ForeignKeyConstraint(
            ["invoice_id", "organization_id"],
            ["invoices.id", "invoices.organization_id"],
            ondelete="RESTRICT",
            name="fk_anomaly_logs_invoice_id_organization_id_invoices",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_anomaly_logs_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="RESTRICT", name="fk_anomaly_logs_resolved_by_users"),
        sa.CheckConstraint(_one_of("severity", SEVERITIES), name="ck_anomaly_logs_severity_valid"),
        sa.CheckConstraint(_one_of("status", ANOMALY_STATUSES), name="ck_anomaly_logs_status_valid"),
        sa.CheckConstraint("confidence_score BETWEEN 0 AND 100", name="ck_anomaly_logs_confidence_range"),
        sa.CheckConstraint(
            "(status = 'OPEN' AND resolved_by IS NULL AND resolved_at IS NULL)"
            " OR (status <> 'OPEN' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_anomaly_logs_resolution_consistent",
        ),
    )
    op.create_index("ix_anomaly_logs_invoice_id", "anomaly_logs", ["invoice_id"])
    op.create_index(
        "ix_anomaly_logs_organization_id_status_severity", "anomaly_logs", ["organization_id", "status", "severity"]
    )
    op.create_index("ix_anomaly_logs_organization_id_anomaly_type", "anomaly_logs", ["organization_id", "anomaly_type"])

    # --- updated_at maintenance -----------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END
        $$
        """
    )
    for table in UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )

    # --- immutable audit trail ------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION anomaly_logs_guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'OPEN' THEN
                RAISE EXCEPTION 'anomaly % is already resolved and cannot be changed', OLD.id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF (NEW.id, NEW.invoice_id, NEW.organization_id, NEW.anomaly_type, NEW.severity,
                NEW.confidence_score, NEW.description, NEW.detected_value, NEW.expected_value, NEW.created_at)
               IS DISTINCT FROM
               (OLD.id, OLD.invoice_id, OLD.organization_id, OLD.anomaly_type, OLD.severity,
                OLD.confidence_score, OLD.description, OLD.detected_value, OLD.expected_value, OLD.created_at)
            THEN
                RAISE EXCEPTION 'anomaly findings are immutable; only the resolution can be recorded'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION anomaly_logs_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- The only legitimate delete is the ON DELETE CASCADE from removing the whole tenant,
            -- in which case the organization row is already gone.
            IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id) THEN
                RAISE EXCEPTION 'anomaly logs are append-only (anomaly %)', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN OLD;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_anomaly_logs_guard_update BEFORE UPDATE ON anomaly_logs "
        "FOR EACH ROW EXECUTE FUNCTION anomaly_logs_guard_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_anomaly_logs_guard_delete BEFORE DELETE ON anomaly_logs "
        "FOR EACH ROW EXECUTE FUNCTION anomaly_logs_guard_delete()"
    )


def downgrade() -> None:
    op.drop_table("anomaly_logs")
    op.drop_table("invoice_items")
    op.drop_table("invoices")
    op.drop_table("tax_rate_limits")
    op.drop_table("vendors")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("organizations")
    op.execute("DROP FUNCTION IF EXISTS anomaly_logs_guard_delete()")
    op.execute("DROP FUNCTION IF EXISTS anomaly_logs_guard_update()")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    # pg_trgm is left installed: other schemas in the database may depend on it.
