"""session metadata (created_at, updated_at, title)

Revision ID: 010
Revises: 009
Create Date: 2026-09-28

Adds first-class session metadata so the chats rail can order by activity and
show a stored title instead of reconstructing both from the message log with
timestamp-equality joins. Existing rows are backfilled from ``messages``.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("created_at", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("updated_at", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("title", sa.String(length=120), nullable=True))

    # Backfill from the message log: first/last message time and first user body.
    op.execute(
        """
        UPDATE sessions AS s
        SET created_at = src.first_at,
            updated_at = src.last_at,
            title = left(src.first_user, 120)
        FROM (
            SELECT m.session_id,
                   min(m.created_at) AS first_at,
                   max(m.created_at) AS last_at,
                   (SELECT m2.content
                      FROM messages AS m2
                     WHERE m2.session_id = m.session_id
                       AND m2.role = 'user'
                     ORDER BY m2.created_at ASC, m2.id ASC
                     LIMIT 1) AS first_user
              FROM messages AS m
             GROUP BY m.session_id
        ) AS src
        WHERE src.session_id = s.id
        """
    )

    # Message-less sessions still need a non-null timestamp pair.
    now = "to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
    op.execute(
        f"""
        UPDATE sessions
           SET created_at = COALESCE(created_at, {now}),
               updated_at = COALESCE(updated_at, created_at, {now})
        """
    )

    op.alter_column("sessions", "created_at", nullable=False)
    op.alter_column("sessions", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("sessions", "title")
    op.drop_column("sessions", "updated_at")
    op.drop_column("sessions", "created_at")
