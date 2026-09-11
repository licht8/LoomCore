"""Общие зависимости и утилиты API-слоя."""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from src.auth import SESSION_COOKIE


def extract_admin_token(
    request: Request,
    authorization: str | None = None,
    access_token: str | None = None,
) -> str | None:
    """Извлечь административный токен из query, cookie или заголовка Authorization."""
    return access_token or request.cookies.get(SESSION_COOKIE) or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )


def chat_key_func(request: Request) -> str:
    """Ключ rate limit для /api/chat — по session_id из query, иначе IP.

    Не читает тело запроса, чтобы не конфликтовать с Pydantic ChatRequest.
    """
    session_id = request.query_params.get("session_id")
    if session_id:
        return session_id
    return request.client.host if request.client else "unknown"


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Кастомный обработчик 429 на русском языке."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Попробуйте позже."},
    )
