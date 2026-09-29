"""session conversation memory

Revision ID: 012
Revises: 011
Create Date: 2026-09-29

Stores the rolling summary of the older part of a conversation on the session,
plus the ``created_at`` of the newest message already folded into it (the
coverage marker, so the same messages are never folded twice).
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("memory", sa.Text(), nullable=True))
    op.add_column(
        "sessions", sa.Column("memory_updated_at", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sessions", "memory_updated_at")
    op.drop_column("sessions", "memory")
