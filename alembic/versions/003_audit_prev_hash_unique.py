"""audit log prev_hash uniqueness

Revision ID: 003
Revises: 002
Create Date: 2026-09-18

A predecessor must have at most one successor. A forked chain (two rows
sharing a prev_hash) is the very state this constraint forbids and could
exist in data written before the append lock (pg_advisory_xact_lock) landed,
so upgrade() checks for duplicates first and aborts with an actionable
message instead of an opaque unique violation. Building the constraint takes
an ACCESS EXCLUSIVE lock, so audit writes are blocked for its duration;
resolve any reported fork offline (keeping the append-only history intact)
before re-running.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT prev_hash FROM audit_log "
            "GROUP BY prev_hash HAVING count(*) > 1"
        )
    ).all()
    if duplicates:
        forked = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            "audit_log contains a forked chain (duplicate prev_hash: "
            f"{forked}); resolve these entries before applying migration 003"
        )
    op.create_unique_constraint("uq_audit_log_prev_hash", "audit_log", ["prev_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_audit_log_prev_hash", "audit_log", type_="unique")
