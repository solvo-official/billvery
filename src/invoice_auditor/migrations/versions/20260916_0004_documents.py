"""Uploaded originals stored in the database.

Serverless platforms have a read-only file system outside /tmp, and /tmp does not survive an
invocation, so deployments there keep the bytes here instead of on disk. Content-addressed per
tenant, exactly like the local store.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documents_sha256_hex"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_documents_organization_id_organizations", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("organization_id", "sha256", name="pk_documents"),
    )


def downgrade() -> None:
    op.drop_table("documents")
