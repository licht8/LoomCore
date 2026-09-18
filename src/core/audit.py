"""Central, tamper-evident audit log for mutating operations.

Every mutation executed through the agent (or the plugin-lifecycle REST routes)
is appended as a hash-chained row: ``entry_hash = sha256(prev_hash | payload)``.
Each entry commits to its predecessor's hash, so removing or editing an entry
breaks the chain and ``verify_chain`` detects it. This is not a signature — a
process with database access could rewrite the whole chain — but any partial
edit is detectable, and the log is central rather than a per-plugin local file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text

from src.db.database import async_session_factory
from src.db.models import AuditEntry

logger = logging.getLogger("loomcore.audit")

GENESIS_HASH = "0" * 64

# Transaction-scoped advisory lock that serializes appends to the chain (see
# record_mutation). It is released automatically at commit/rollback.
_AUDIT_LOCK_STATEMENT = text("SELECT pg_advisory_xact_lock(hashtext('loomcore.audit'))")


def _canonical(payload: dict[str, Any]) -> str:
    """Deterministic JSON so the hash does not depend on key ordering."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def entry_payload(
    *,
    created_at: str,
    actor: str,
    session_id: str | None,
    plan_id: str | None,
    revision: int | None,
    tool: str,
    risk: str | None,
    arguments: dict | None,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    """The immutable fields covered by the entry hash (everything but hashes)."""
    return {
        "created_at": created_at,
        "actor": actor,
        "session_id": session_id,
        "plan_id": plan_id,
        "revision": revision,
        "tool": tool,
        "risk": risk,
        "arguments": arguments,
        "status": status,
        "error": error,
    }


def compute_entry_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    digest = f"{prev_hash}|{_canonical(payload)}"
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


async def record_mutation(
    session_db: Any,
    *,
    tool: str,
    arguments: dict | None = None,
    risk: str | None = None,
    status: str,
    actor: str = "admin",
    session_id: str | None = None,
    plan_id: str | None = None,
    revision: int | None = None,
    error: str | None = None,
) -> AuditEntry | None:
    """Append one hash-chained audit row. Best-effort: never raises."""
    try:
        # Serialize the read+insert so two concurrent writers cannot both chain
        # to the same predecessor and fork the chain. SELECT ... FOR UPDATE is
        # not enough: a row inserted by a concurrent transaction is invisible to
        # it, so the second writer would still read the stale predecessor. The
        # lock is held until this transaction's commit/rollback, so the select
        # below sees the predecessor committed by a writer that held it before.
        await session_db.execute(_AUDIT_LOCK_STATEMENT)
        result = await session_db.execute(
            select(AuditEntry)
            .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        prev_hash = last.entry_hash if last is not None else GENESIS_HASH

        created_at = datetime.now(UTC).isoformat()
        payload = entry_payload(
            created_at=created_at,
            actor=actor,
            session_id=session_id,
            plan_id=plan_id,
            revision=revision,
            tool=tool,
            risk=risk,
            arguments=arguments,
            status=status,
            error=error,
        )
        entry = AuditEntry(
            created_at=created_at,
            actor=actor,
            session_id=session_id,
            plan_id=plan_id,
            revision=revision,
            tool=tool,
            risk=risk,
            arguments=arguments,
            status=status,
            error=error,
            prev_hash=prev_hash,
            entry_hash=compute_entry_hash(prev_hash, payload),
        )
        session_db.add(entry)
        await session_db.commit()
        await session_db.refresh(entry)
        return entry
    except Exception as exc:
        logger.warning("audit: failed to record the %s mutation: %s", tool, exc)
        with contextlib.suppress(Exception):
            await session_db.rollback()
        return None


async def record_mutation_isolated(**kwargs: Any) -> AuditEntry | None:
    """Record a mutation on a dedicated session so it never blocks the caller."""
    try:
        async with async_session_factory() as session_db:
            return await record_mutation(session_db, **kwargs)
    except Exception as exc:
        logger.warning(
            "audit: isolated record failed for %s: %s", kwargs.get("tool"), exc
        )
        return None


def verify_chain(entries: list[AuditEntry]) -> bool:
    """Return True when every entry links correctly to the previous one."""
    prev = GENESIS_HASH
    for entry in entries:
        payload = entry_payload(
            created_at=entry.created_at,
            actor=entry.actor,
            session_id=entry.session_id,
            plan_id=entry.plan_id,
            revision=entry.revision,
            tool=entry.tool,
            risk=entry.risk,
            arguments=entry.arguments,
            status=entry.status,
            error=entry.error,
        )
        if entry.prev_hash != prev:
            return False
        if entry.entry_hash != compute_entry_hash(prev, payload):
            return False
        prev = entry.entry_hash
    return True
