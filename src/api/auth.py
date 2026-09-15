"""Admin authentication routes."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth import (
    SESSION_COOKIE,
    create_jwt_token,
    decode_jwt_token,
    revoke_jwt_token,
    verify_password,
    verify_totp,
)
from src.config import settings
from src.core.client_ip import client_ip
from src.core.rate_limit import limiter
from src.core.security import admin_jwt_mode_enabled, auth_mode
from src.core.security_log import log_security_event

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _is_https(request: Request) -> bool:
    """Return True when the request reached us over TLS (directly or via a proxy)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


@router.post("/login")
@limiter.limit("5 per 60 seconds")
async def login(request: Request):
    if settings.require_tls and not _is_https(request):
        log_security_event(
            "auth_failure", reason="tls_required", client=client_ip(request)
        )
        raise HTTPException(status_code=403, detail="TLS is required")
    if auth_mode() == "token":
        log_security_event(
            "login_failure", reason="auth_mode_token", client=client_ip(request)
        )
        raise HTTPException(
            status_code=403, detail="Password login is disabled (AUTH_MODE=token)"
        )
    data = await request.json()
    valid = (
        data.get("username") == settings.admin_username
        and admin_jwt_mode_enabled()
        and verify_password(str(data.get("password", "")), settings.admin_password_hash)
        and verify_totp(str(data.get("totp", "")), settings.admin_totp_secret)
    )
    if not valid:
        log_security_event(
            "login_failure",
            username=data.get("username"),
            client=client_ip(request),
        )
        raise HTTPException(status_code=401, detail="Invalid login credentials")
    if not settings.session_secret:
        log_security_event(
            "login_failure", reason="session_secret_missing", client=client_ip(request)
        )
        raise HTTPException(status_code=503, detail="SESSION_SECRET is not configured")
    token = create_jwt_token(
        {"username": settings.admin_username, "jti": secrets.token_urlsafe(32)},
        settings.session_secret,
    )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=(
            settings.secure_cookies
            or request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        ),
        samesite="strict",
        max_age=28800,
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    payload = decode_jwt_token(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    if payload:
        revoke_jwt_token(payload.get("jti"))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/session")
async def auth_session(request: Request):
    payload = decode_jwt_token(request.cookies.get(SESSION_COOKIE), settings.session_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"authenticated": True}
