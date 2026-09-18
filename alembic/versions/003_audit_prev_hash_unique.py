"""audit log prev_hash uniqueness

Revision ID: 003
Revises: 002
Create Date: 2026-09-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_audit_log_prev_hash", "audit_log", ["prev_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_audit_log_prev_hash", "audit_log", type_="unique")
