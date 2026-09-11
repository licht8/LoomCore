import asyncio
import uuid
import base64
import secrets
import logging

import uvicorn
from fastapi import FastAPI, Depends, WebSocket, HTTPException, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.rate_limit import limiter
from src.db.database import get_db, init_db
from src.interfaces.websocket import websocket_endpoint
from src.llm.router import AIRouter
from src.plugins import plugin_manager
from src.auth import SESSION_COOKIE, create_session, revoke_session, verify_password, verify_session, verify_totp

logger = logging.getLogger("loomhub.main")


def _chat_key_func(request: Request) -> str:
    """Ключ для /api/chat — по session_id из query-параметра.

    Не читает тело запроса, чтобы не конфликтовать с Pydantic ChatRequest.
    """
    session_id = request.query_params.get("session_id")
    if session_id:
        return session_id
    return request.client.host if request.client else "unknown"


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Кастомный обработчик 429 на русском языке."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Попробуйте позже."},
    )


app = FastAPI(
    title="LoomHub Core",
    version="0.0.1",
    docs_url="/docs" if settings.debug else None,
)

# Регистрируем обработчик 429
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Подключение CORS для React (Vite/Next.js) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8083",
        "http://127.0.0.1:8083",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

airouter = AIRouter(plugin_manager=plugin_manager)


class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str


def require_admin_token(token: str | None) -> None:
    if settings.admin_password_hash and settings.admin_totp_secret:
        if not token or not verify_session(token, settings.session_secret):
            raise HTTPException(status_code=401, detail="Требуется вход администратора")
        return
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Административный доступ не настроен")
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Требуется административный токен")


@app.post("/api/auth/login")
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
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        create_session(settings.session_secret),
        httponly=True,
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        samesite="strict",
        max_age=28800,
    )
    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    revoke_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/session")
async def auth_session(request: Request):
    if not verify_session(request.cookies.get(SESSION_COOKIE), settings.session_secret):
        raise HTTPException(status_code=401, detail="Не авторизован")
    return {"authenticated": True}


@app.on_event("startup")
async def startup():
    print("🚀 LoomHub Core starting up...")
    await init_db()

    discovered = plugin_manager.discover_plugins("/app/plugins")
    print(f"🔌 Plugins loaded: {', '.join(discovered) or 'none'}")

    # Запускаем фоновый healthcheck каждые 30 секунд
    asyncio.create_task(_healthcheck_loop())

    # Поднимаем WireGuard, если конфиг есть, но интерфейс не активен
    asyncio.create_task(_ensure_wg_interface())


async def _ensure_wg_interface() -> None:
    """Поднять WireGuard-интерфейс, если конфиг существует."""
    from pathlib import Path
    import subprocess

    config_path = Path("/etc/wireguard/wg0.conf")
    if not config_path.is_file():
        return
    try:
        result = subprocess.run(
            ["wg", "show", "interfaces"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.stdout.strip().split()
        if "wg0" in active:
            return
        logger.info("Конфиг wg0 существует, но интерфейс не активен — поднимаю")
        subprocess.run(
            ["wg-quick", "up", "wg0"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.info("WireGuard wg0 поднят")
    except Exception as exc:
        logger.warning(f"Не удалось поднять wg0: {exc}")


async def _healthcheck_loop():
    """Фоновая задача: проверяет здоровье всех плагинов каждые 30 секунд.

    Если плагин не здоров (процесс мёртв) — пытается автоперезапуск
    асинхронно, не блокируя обработчики API-запросов.
    """
    while True:
        await asyncio.sleep(30)
        for name in plugin_manager.plugin_names():
            if not plugin_manager.is_healthy(name):
                logger.warning(f"Плагин '{name}' не здоров — перезапуск")
                try:
                    await plugin_manager._restart_plugin(name)
                    logger.info(f"Плагин '{name}' успешно перезапущен")
                except Exception as exc:
                    logger.error(f"Не удалось перезапустить '{name}': {exc}")


@app.get("/health")
@limiter.limit("60 per 60 seconds")
def health(request: Request):
    return {"status": "ok"}


@app.get("/api/plugins/status")
@limiter.limit("10 per 60 seconds")
async def get_plugins_status(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Вернуть состояние всех плагинов."""
    token = access_token or request.cookies.get(SESSION_COOKIE) or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    require_admin_token(token)
    statuses = {}
    for name in plugin_manager.plugin_names():
        statuses[name] = plugin_manager.get_plugin_status(name)
    return {"plugins": statuses}


@app.get("/api/tools")
@limiter.limit("10 per 60 seconds")
async def list_plugins_tools(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Вернуть схемы инструментов всех плагинов (для Swagger/UI)."""
    token = access_token or request.cookies.get(SESSION_COOKIE) or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    require_admin_token(token)
    result = {}
    try:
        result["tools"] = await plugin_manager.get_all_tools()
    except Exception as exc:
        result["loomwg"] = {"error": str(exc)}
    return result


@app.post("/api/chat")
@limiter.limit("30 per 60 seconds", key_func=_chat_key_func)
async def chat_endpoint(
    request: ChatRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Отправить сообщение AI и получить ответ (REST)."""
    token = access_token or http_request.cookies.get(SESSION_COOKIE) or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    require_admin_token(token)
    try:
        result = await airouter.route(request.message, request.session_id)
        return result
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )


@app.get("/api/loomwg/artifacts/{token}/{kind}")
async def download_loomwg_artifact(
    token: str,
    kind: str,
    request: Request,
    access_token: str | None = Query(default=None),
):
    """Consume a short-lived, one-time client artifact capability."""
    require_admin_token(access_token or request.cookies.get(SESSION_COOKIE))
    if kind not in {"config", "qr"}:
        return JSONResponse(status_code=404, content={"error": "Артефакт не найден"})
    try:
        result = await plugin_manager.execute_tool(
            "loomwg",
            "wg_get_client_artifact",
            {"token": token, "kind": kind},
        )
        data = result.get("data", {})
        payload = base64.b64decode(data["content_base64"], validate=True)
        return Response(
            content=payload,
            media_type=data["media_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{data["filename"]}"',
                "Cache-Control": "no-store",
            },
        )
    except (KeyError, ValueError, RuntimeError, OSError):
        return JSONResponse(status_code=404, content={"error": "Артефакт не найден или истёк"})


@app.websocket("/ws/{session_id}")
async def ws_proxy(websocket: WebSocket, session_id: str, access_token: str | None = Query(default=None)):
    """Прокси-роутер: передаёт управление в src.interfaces.websocket."""
    cookie = websocket.cookies.get(SESSION_COOKIE)
    authorized = (
        verify_session(cookie, settings.session_secret)
        if settings.admin_password_hash and settings.admin_totp_secret
        else bool(settings.admin_token and access_token and secrets.compare_digest(access_token, settings.admin_token))
    )
    if not authorized:
        await websocket.close(code=1008, reason="Требуется административный токен")
        return
    await websocket_endpoint(websocket, session_id)


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
