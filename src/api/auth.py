"""Admin authentication routes."""

import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.deps import extract_admin_token
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
from src.core.security import (
    admin_jwt_mode_enabled,
    auth_mode,
    is_https,
    require_admin_token,
)
from src.core.security_log import log_security_event

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
@limiter.limit("5 per 60 seconds")
async def login(request: Request):
    if settings.require_tls and not is_https(request):
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
    try:
        data = await request.json()
    except Exception:
        log_security_event(
            "login_failure", reason="invalid_json_body", client=client_ip(request)
        )
        raise HTTPException(
            status_code=400, detail="Invalid JSON body"
        ) from None
    if not isinstance(data, dict):
        log_security_event(
            "login_failure", reason="invalid_body_shape", client=client_ip(request)
        )
        raise HTTPException(status_code=400, detail="Invalid request body")
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
async def auth_session(request: Request, authorization: str | None = Header(default=None)):
    """Report whether the caller holds a valid admin credential.

    Accepts either credential the active auth mode allows: the httpOnly session
    cookie (JWT, the pre-existing path) or the static Bearer token (so the token
    login can verify itself against this non-rate-limited endpoint). A valid
    credential the active mode disables gets 403 rather than being silently
    accepted.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie and decode_jwt_token(cookie, settings.session_secret):
        return {"authenticated": True}

    if authorization and authorization.startswith("Bearer "):
        require_admin_token(extract_admin_token(request, authorization), request)
        return {"authenticated": True}

    raise HTTPException(status_code=401, detail="Not authenticated")
