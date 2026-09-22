"""Admin authentication routes."""

import asyncio
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.deps import extract_admin_token
from src.auth import (
    SESSION_COOKIE,
    create_jwt_token,
    decode_jwt_token,
    generate_password_hash,
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
    is_valid_admin_credential,
    require_admin_token,
)
from src.core.security_log import log_security_event

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Consumed by verify_password when no admin hash is configured so a login still
# pays the full PBKDF2 cost (constant-work login; the value is never valid).
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


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
    username_ok = secrets.compare_digest(
        str(data.get("username") or "").encode("utf-8"),
        (settings.admin_username or "").encode("utf-8"),
    )
    password_ok = await asyncio.to_thread(
        verify_password,
        str(data.get("password", "")),
        settings.admin_password_hash or _DUMMY_PASSWORD_HASH,
    )
    totp_ok = verify_totp(str(data.get("totp", "")), settings.admin_totp_secret)
    valid = username_ok and admin_jwt_mode_enabled() and password_ok and totp_ok
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
        secure=settings.secure_cookies or is_https(request),
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
@limiter.limit("30 per 60 seconds")
async def auth_session(request: Request, authorization: str | None = Header(default=None)):
    """Report whether the caller holds a valid admin credential.

    Accepts either credential the active auth mode allows: the httpOnly session
    cookie (JWT, the pre-existing path) or the static Bearer token (so the token
    login can verify itself against this endpoint). A valid credential the active
    mode disables gets 403 rather than being silently accepted. Rate-limited to
    30 requests/min per client IP so it cannot serve as an unthrottled
    credential-guessing oracle.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if is_valid_admin_credential(cookie):
        return {"authenticated": True}

    if authorization and authorization.lower().startswith("bearer "):
        require_admin_token(extract_admin_token(request, authorization), request)
        return {"authenticated": True}

    raise HTTPException(status_code=401, detail="Not authenticated")
