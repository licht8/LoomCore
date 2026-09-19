"""widen session id columns to 64 chars

Revision ID: 006
Revises: 005
Create Date: 2026-09-19

The chat request contract accepts session ids up to 64 chars, and the frontend
already emits ``sess_<32 hex>`` (37 chars, frontend/lib/chat-api.ts). The
persistence columns were ``String(36)``, so such an id overflowed on INSERT
(``ensure_session`` -> value-too-long ``DataError`` -> generic 500). Widen
``sessions.id``, ``messages.session_id`` and ``audit_log.session_id`` to
``String(64)``; nullability is unchanged.

Postgres refuses to alter the type of a column referenced by a foreign key
while the FK exists, so ``messages_session_id_fkey`` is dropped and recreated
around the two column alters (``audit_log.session_id`` has no FK).

``downgrade()`` shrinks back to ``String(36)`` and will fail if any row is
longer than 36 chars — delete/shorten such rows first.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

_FK_NAME = "messages_session_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK_NAME, "messages", type_="foreignkey")
    op.alter_column(
        "sessions", "id", existing_type=sa.String(36), type_=sa.String(64)
    )
    op.alter_column(
        "messages",
        "session_id",
        existing_type=sa.String(36),
        type_=sa.String(64),
    )
    op.alter_column(
        "audit_log",
        "session_id",
        existing_type=sa.String(36),
        type_=sa.String(64),
    )
    op.create_foreign_key(_FK_NAME, "messages", "sessions", ["session_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(_FK_NAME, "messages", type_="foreignkey")
    op.alter_column(
        "audit_log",
        "session_id",
        existing_type=sa.String(64),
        type_=sa.String(36),
    )
    op.alter_column(
        "messages",
        "session_id",
        existing_type=sa.String(64),
        type_=sa.String(36),
    )
    op.alter_column(
        "sessions", "id", existing_type=sa.String(64), type_=sa.String(36)
    )
    op.create_foreign_key(_FK_NAME, "messages", "sessions", ["session_id"], ["id"])
