"""Роуты состояния плагинов и их инструментов."""

from fastapi import APIRouter, Header, Query, Request

from src.api.deps import extract_admin_token
from src.core.rate_limit import limiter
from src.core.security import require_admin_token
from src.plugins import plugin_manager

router = APIRouter(prefix="/api", tags=["plugins"])


@router.get("/plugins/status")
@limiter.limit("10 per 60 seconds")
async def get_plugins_status(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Вернуть состояние всех плагинов."""
    require_admin_token(extract_admin_token(request, authorization, access_token))
    return {
        "plugins": {
            name: plugin_manager.get_plugin_status(name)
            for name in plugin_manager.plugin_names()
        }
    }


@router.get("/tools")
@limiter.limit("10 per 60 seconds")
async def list_plugins_tools(
    request: Request,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Вернуть схемы инструментов всех плагинов (для Swagger/UI)."""
    require_admin_token(extract_admin_token(request, authorization, access_token))
    result: dict = {}
    try:
        result["tools"] = await plugin_manager.get_all_tools()
    except Exception as exc:
        result["error"] = str(exc)
    return result
