"""not-null defaults and foreign-key indexes

Revision ID: 004
Revises: 003
Create Date: 2026-09-18

Migration 001 created `users.provider` / `users.role` / `sessions.status` /
`messages.content` with a server_default or as plain nullable columns, and never
indexed the foreign-key columns `sessions.user_id` / `messages.session_id`.
Migration 002 likewise left `audit_log.actor` nullable. The ORM declares all of
these non-optional (`Mapped[str]`) and now sets `index=True` where a migration
creates an index, so existing installations that already applied 001/002 must be
brought in line here — editing the earlier revisions in place would only affect
fresh installs and leave them permanently drifted. Legacy NULLs are backfilled
with the documented defaults (or `''` where there is none) before SET NOT NULL.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET provider = 'web' WHERE provider IS NULL")
    op.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
    op.execute("UPDATE sessions SET status = 'active' WHERE status IS NULL")
    op.execute("UPDATE messages SET content = '' WHERE content IS NULL")
    op.execute("UPDATE audit_log SET actor = 'admin' WHERE actor IS NULL")

    op.alter_column("users", "provider", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "role", existing_type=sa.String(), nullable=False)
    op.alter_column("sessions", "status", existing_type=sa.String(), nullable=False)
    op.alter_column("messages", "content", existing_type=sa.Text(), nullable=False)
    op.alter_column("audit_log", "actor", existing_type=sa.String(), nullable=False)

    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_messages_session_id", "messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_index("ix_sessions_user_id", table_name="sessions")

    op.alter_column("audit_log", "actor", existing_type=sa.String(), nullable=True)
    op.alter_column("messages", "content", existing_type=sa.Text(), nullable=True)
    op.alter_column("sessions", "status", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "role", existing_type=sa.String(), nullable=True)
    op.alter_column("users", "provider", existing_type=sa.String(), nullable=True)
