"""Shared dependencies and utilities for the API layer."""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src.auth import SESSION_COOKIE


def extract_admin_token(
    request: Request,
    authorization: str | None = None,
    access_token: str | None = None,
) -> str | None:
    """Extract the admin token from query, cookie, or Authorization header."""
    return access_token or request.cookies.get(SESSION_COOKIE) or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )


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
