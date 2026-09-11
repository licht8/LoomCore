"""Роуты авторизации администратора."""

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
from src.core.rate_limit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
@limiter.limit("5 per 60 seconds")
async def login(request: Request):
    data = await request.json()
    valid = (
        data.get("username") == settings.admin_username
        and bool(settings.admin_password_hash and settings.admin_totp_secret)
        and verify_password(str(data.get("password", "")), settings.admin_password_hash)
        and verify_totp(str(data.get("totp", "")), settings.admin_totp_secret)
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Неверные данные входа")
    if not settings.session_secret:
        raise HTTPException(status_code=503, detail="SESSION_SECRET не настроен")
    token = create_jwt_token(
        {"username": settings.admin_username, "jti": secrets.token_urlsafe(32)},
        settings.session_secret,
    )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
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
        raise HTTPException(status_code=401, detail="Не авторизован")
    return {"authenticated": True}
