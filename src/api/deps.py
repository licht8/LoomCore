"""Shared dependencies and utilities for the API layer."""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src.auth import SESSION_COOKIE
from src.core.security import require_admin_token


def extract_admin_token(
    request: Request,
    authorization: str | None = None,
) -> str | None:
    """Extract the admin token from the session cookie or Bearer header.

    Query-string tokens are intentionally NOT accepted: a token in the URL
    leaks through access logs, browser history and Referer headers.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def require_admin_token_from_request(
    request: Request,
    authorization: str | None = None,
) -> None:
    """Validate the admin token extracted from a request (shared helper)."""
    require_admin_token(extract_admin_token(request, authorization))


def chat_key_func(request: Request) -> str:
    """Rate limit key for /api/chat — by session_id from the query, else IP.

    Does not read the request body to avoid conflicting with Pydantic ChatRequest.
    """
    session_id = request.query_params.get("session_id")
    if session_id:
        return session_id
    return request.client.host if request.client else "unknown"


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Custom 429 handler."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )
