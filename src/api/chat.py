"""REST chat route with the AI."""

import logging
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.deps import chat_key_func, extract_admin_token
from src.core.rate_limit import limiter
from src.core.security import require_admin_token
from src.llm.client import LlmError
from src.llm.exceptions import LlmBudgetExceeded
from src.llm.router import AIRouter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.api.chat")

router = APIRouter(prefix="/api", tags=["chat"])

airouter = AIRouter(plugin_manager=plugin_manager)


class ChatRequest(BaseModel):
    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
    )
    message: str = Field(min_length=1, max_length=8000)


@router.post("/chat")
@limiter.limit("30 per 60 seconds", key_func=chat_key_func)
async def chat_endpoint(
    payload: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Send a message to the AI and get a response (REST)."""
    require_admin_token(extract_admin_token(request, authorization), request)
    try:
        return await airouter.route(payload.message, payload.session_id)
    except LlmBudgetExceeded as exc:
        logger.warning("LLM budget exceeded (session=%s): %s", payload.session_id, exc)
        return JSONResponse(status_code=429, content={"detail": str(exc)})
    except LlmError as exc:
        logger.warning("LLM error (session=%s): %s", payload.session_id, exc)
        return JSONResponse(status_code=504, content={"error": str(exc)})
    except Exception:
        logger.exception("chat request failed (session=%s)", payload.session_id)
        return JSONResponse(
            status_code=500, content={"error": "Internal server error"}
        )
