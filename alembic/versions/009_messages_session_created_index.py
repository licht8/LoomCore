"""messages session/time index

Revision ID: 009
Revises: 008
Create Date: 2026-09-22

Adds ``ix_messages_session_id_created_at`` so session-history reads and the
session list can seek by ``(session_id, created_at)`` instead of scanning.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_session_id_created_at",
        "messages",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_session_id_created_at", table_name="messages")
