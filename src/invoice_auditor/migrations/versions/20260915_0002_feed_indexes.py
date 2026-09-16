"""Indexes for the invoice feed: keyset pages by received time, polling by change time.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_invoices_organization_id_created_at_id", "invoices", ["organization_id", "created_at", "id"])
    op.create_index("ix_invoices_organization_id_updated_at", "invoices", ["organization_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_invoices_organization_id_updated_at", table_name="invoices")
    op.drop_index("ix_invoices_organization_id_created_at_id", table_name="invoices")
