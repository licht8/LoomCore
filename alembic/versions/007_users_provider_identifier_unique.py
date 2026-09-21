"""scope user identity to (provider, identifier)

Revision ID: 007
Revises: 006
Create Date: 2026-09-21

Migration 001 created an unnamed single-column UNIQUE on ``users.identifier``
(Postgres default name ``users_identifier_key``), so a web identity and a
Telegram identity that share the same string could not coexist. This replaces it
with ``uq_users_provider_identifier`` on ``(provider, identifier)``. The existing
constraint is located in the catalog rather than assumed by name, with the
documented default as a fallback.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

_FALLBACK_UNIQUE = "users_identifier_key"

_IDENTIFIER_UNIQUE_STMT = sa.text(
    """
    SELECT c.conname
    FROM pg_constraint AS c
    JOIN pg_class AS t ON t.oid = c.conrelid
    JOIN pg_attribute AS a
      ON a.attrelid = c.conrelid
     AND a.attname = 'identifier'
    WHERE t.relname = 'users'
      AND c.contype = 'u'
      AND c.conkey = ARRAY[a.attnum]
    """,
)

_DUPLICATE_IDENTIFIER_STMT = sa.text(
    """
    SELECT identifier, COUNT(*) AS copies
    FROM users
    GROUP BY identifier
    HAVING COUNT(*) > 1
    LIMIT 1
    """,
)


def upgrade() -> None:
    existing = op.get_bind().execute(_IDENTIFIER_UNIQUE_STMT).scalar()
    op.drop_constraint(existing or _FALLBACK_UNIQUE, "users", type_="unique")
    op.create_unique_constraint(
        "uq_users_provider_identifier", "users", ["provider", "identifier"]
    )


def downgrade() -> None:
    duplicate = op.get_bind().execute(_DUPLICATE_IDENTIFIER_STMT).first()
    if duplicate is not None:
        raise RuntimeError(
            f"Cannot downgrade 007: identifier {duplicate[0]!r} exists under "
            f"multiple providers ({duplicate[1]} rows). Merge or delete the "
            "duplicates before restoring the single-column UNIQUE on "
            "users.identifier."
        )
    op.drop_constraint("uq_users_provider_identifier", "users", type_="unique")
    op.create_unique_constraint(_FALLBACK_UNIQUE, "users", ["identifier"])
