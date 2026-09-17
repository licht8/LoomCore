"""Shared dependencies and utilities for the API layer."""

import hashlib

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src.auth import SESSION_COOKIE
from src.core.client_ip import client_ip
from src.core.security import is_valid_admin_credential, require_admin_token
from src.core.security_log import log_security_event


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
    require_admin_token(extract_admin_token(request, authorization), request)


def chat_key_func(request: Request) -> str:
    """Rate limit key for /api/chat — the authenticated principal, not the client.

    The key is derived server-side from the credential (session cookie or
    Bearer token) as a hash, so rotating a client-supplied ``session_id`` can no
    longer reset the limit. The credential is validated first: an unknown or
    missing one falls back to the remote address, otherwise an unauthenticated
    caller could send a fresh bogus cookie on every request and get a new bucket
    each time.
    """
    token = extract_admin_token(request, request.headers.get("authorization"))
    if token and is_valid_admin_credential(token):
        return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    return "ip:" + client_ip(request)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Custom 429 handler (also emits a security event for detection)."""
    log_security_event(
        "rate_limit_exceeded",
        client=client_ip(request),
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )
