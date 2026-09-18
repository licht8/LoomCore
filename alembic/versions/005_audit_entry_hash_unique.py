"""audit log entry_hash uniqueness

Revision ID: 005
Revises: 004
Create Date: 2026-09-19

The tamper evidence of the hash chain assumes each ``entry_hash`` is unique, but
nothing enforced it: a replayed or copied insert (e.g. a row duplicated from an
existing entry) was accepted, and there was no index to look an entry up by
hash. Migration 002 is already applied on existing installations, so this is a
new revision rather than an in-place edit. Like 003 it takes the append lock,
checks for duplicates and aborts with an actionable message instead of an opaque
unique violation; the unique constraint also creates the lookup index. Building
it takes an ACCESS EXCLUSIVE lock, so audit writes are blocked for its duration.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Serialize against the append path (src/core/audit.py) so no writer can
    # commit a duplicate between the check below and the constraint being built.
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('loomcore.audit'))"))
    duplicates = bind.execute(
        sa.text(
            "SELECT entry_hash FROM audit_log "
            "GROUP BY entry_hash HAVING count(*) > 1"
        )
    ).all()
    if duplicates:
        forked = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            "audit_log contains duplicate entry_hash values "
            f"({forked}); resolve these entries before applying migration 005"
        )
    op.create_unique_constraint("uq_audit_log_entry_hash", "audit_log", ["entry_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_audit_log_entry_hash", "audit_log", type_="unique")
