"""llm usage

Revision ID: 008
Revises: 007
Create Date: 2026-09-22

Adds ``llm_usage``: one row per completed LLM call (success or terminal
failure) with tokens and latency. No foreign keys so rows survive session
deletion, mirroring ``audit_log``. Token columns are nullable because some
OpenAI-compatible gateways omit ``usage``.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(256), nullable=False, server_default=""),
        sa.Column("model", sa.String(256), nullable=False, server_default=""),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="chat"),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="success"),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index("ix_llm_usage_model", "llm_usage", ["model"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_model", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
