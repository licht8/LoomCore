"""Read-only routes for the hash-chained audit log."""

from fastapi import APIRouter, Header, Query, Request

from src.api.deps import require_admin_token_from_request
from src.core.audit import verify_chain
from src.core.rate_limit import limiter
from src.db.audit_store import (
    all_audit_entries,
    count_audit_entries,
    list_audit_entries,
)
from src.db.database import async_session_factory

router = APIRouter(prefix="/api", tags=["audit"])


def _serialize(entry) -> dict:
    """Convert a stored AuditEntry row into the API payload shape.

    The hashes (``prev_hash``/``entry_hash``) are intentionally not exposed.
    """
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "actor": entry.actor,
        "session_id": entry.session_id,
        "plan_id": entry.plan_id,
        "revision": entry.revision,
        "tool": entry.tool,
        "risk": entry.risk,
        "arguments": entry.arguments,
        "status": entry.status,
        "error": entry.error,
    }


@router.get("/audit")
@limiter.limit("60 per 60 seconds")
async def list_audit(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None),
):
    """Return a page of audit entries (newest first) plus the total count."""
    require_admin_token_from_request(request, authorization)
    async with async_session_factory() as db:
        entries = await list_audit_entries(db, limit=limit, offset=offset)
        total = await count_audit_entries(db)
    return {"entries": [_serialize(entry) for entry in entries], "total": total}


@router.get("/audit/verify")
@limiter.limit("60 per 60 seconds")
async def verify_audit(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Verify the full hash chain (tamper-evidence, not truncation).

    ``verify_chain`` is called without an independently known anchor, so a
    truncated log still validates; the endpoint reports tamper-evidence only.
    """
    require_admin_token_from_request(request, authorization)
    async with async_session_factory() as db:
        entries = await all_audit_entries(db)
    valid = verify_chain(entries)
    head = entries[-1].entry_hash if entries else None
    return {"valid": valid, "count": len(entries), "head": head}
