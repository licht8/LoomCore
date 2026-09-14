"""audit log

Revision ID: 002
Revises: 001
Create Date: 2026-02-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("actor", sa.String, server_default="admin"),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("plan_id", sa.String, nullable=True),
        sa.Column("revision", sa.Integer, nullable=True),
        sa.Column("tool", sa.String, nullable=False),
        sa.Column("risk", sa.String, nullable=True),
        sa.Column("arguments", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_audit_log_created_at",
        "audit_log",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
