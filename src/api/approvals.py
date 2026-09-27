"""Read-only route listing plans awaiting an admin decision."""

from fastapi import APIRouter, Header, Query, Request

from src.api.deps import require_admin_token_from_request
from src.core.rate_limit import limiter
from src.db.database import async_session_factory
from src.db.session import list_pending_plans

router = APIRouter(prefix="/api", tags=["approvals"])


@router.get("/approvals")
@limiter.limit("60 per 60 seconds")
async def list_approvals(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    authorization: str | None = Header(default=None),
):
    """List plans with status ``proposed`` (newest revision per plan)."""
    require_admin_token_from_request(request, authorization)
    async with async_session_factory() as db:
        approvals = await list_pending_plans(db, limit=limit)
    return {"approvals": approvals}
