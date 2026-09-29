"""session pending plan

Revision ID: 011
Revises: 010
Create Date: 2026-09-29

Persists the single plan awaiting the user's textual approval, so a page reload
or WS reconnect does not lose it and a ``да`` can be bound to the exact plan the
user saw. Value shape: ``{plan, user_text, created_at}``.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("pending", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "pending")
