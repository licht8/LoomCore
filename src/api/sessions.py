"""REST routes for reading and clearing a chat session's stored history."""

import logging
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request

from src.api.deps import require_admin_token_from_request
from src.db.database import async_session_factory
from src.db.session import clear_session_history, get_session_history

logger = logging.getLogger("loomcore.api.sessions")

router = APIRouter(prefix="/api", tags=["sessions"])

_SESSION_ID = Annotated[
    str, Path(max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
]


def _serialize(message) -> dict:
    """Convert a stored Message row into the API payload shape."""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "tool_calls": message.tool_calls,
        "tool_result": message.tool_result,
    }


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: _SESSION_ID,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    authorization: str | None = Header(default=None),
):
    """Return the stored history of a chat session (oldest first)."""
    require_admin_token_from_request(request, authorization)
    async with async_session_factory() as db:
        messages = await get_session_history(db, session_id, limit=limit)
    return {"session_id": session_id, "messages": [_serialize(m) for m in messages]}


@router.delete("/sessions/{session_id}/messages")
async def delete_messages(
    session_id: _SESSION_ID,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Delete all messages in a session (resets the assistant's memory)."""
    require_admin_token_from_request(request, authorization)
    async with async_session_factory() as db:
        deleted = await clear_session_history(db, session_id)
    logger.info("Cleared %s messages for session %s", deleted, session_id)
    return {"session_id": session_id, "deleted": deleted}
