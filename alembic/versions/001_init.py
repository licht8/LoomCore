"""init

Revision ID: 001
Revises:
Create Date: 2026-02-14

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("identifier", sa.String, unique=True, nullable=False),
        sa.Column("provider", sa.String, server_default="web"),
        sa.Column("role", sa.String, server_default="user"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String, server_default="active"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("tool_calls", postgresql.JSONB),
        sa.Column("tool_result", sa.Text),
        sa.Column("created_at", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("users")
