"""Routes for plugin and tool status."""

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.deps import extract_admin_token
from src.core.core_tools import CORE_TOOLS_SCHEMA
from src.core.rate_limit import limiter
from src.core.security import require_admin_token
from src.plugins import plugin_manager

router = APIRouter(prefix="/api", tags=["plugins"])


@router.get("/plugins/status")
@limiter.limit("10 per 60 seconds")
async def get_plugins_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the status of all plugins."""
    require_admin_token(extract_admin_token(request, authorization))
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
):
    """Return the tool schemas of all plugins (for Swagger/UI)."""
    require_admin_token(extract_admin_token(request, authorization))
    result: dict = {}
    try:
        result["tools"] = list(CORE_TOOLS_SCHEMA) + await plugin_manager.get_all_tools()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _require_plugin(plugin_id: str) -> None:
    if plugin_id not in plugin_manager.plugin_names():
        raise HTTPException(
            status_code=404, detail=f"Plugin '{plugin_id}' is not registered"
        )


@router.post("/plugins/{plugin_id}/enable")
@limiter.limit("10 per 60 seconds")
async def enable_plugin(
    plugin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Enable and start a disabled plugin (admin only)."""
    require_admin_token(extract_admin_token(request, authorization))
    _require_plugin(plugin_id)
    if not await plugin_manager.enable_plugin(plugin_id):
        raise HTTPException(status_code=500, detail=f"Failed to enable '{plugin_id}'")
    return {"plugin": plugin_id, "enabled": True}


@router.post("/plugins/{plugin_id}/disable")
@limiter.limit("10 per 60 seconds")
async def disable_plugin(
    plugin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Stop a plugin and persist its disabled state (admin only)."""
    require_admin_token(extract_admin_token(request, authorization))
    _require_plugin(plugin_id)
    if not await plugin_manager.disable_plugin(plugin_id):
        raise HTTPException(status_code=500, detail=f"Failed to disable '{plugin_id}'")
    return {"plugin": plugin_id, "enabled": False}


@router.post("/plugins/{plugin_id}/reload")
@limiter.limit("10 per 60 seconds")
async def reload_plugin(
    plugin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Restart an enabled plugin and refresh its tools (admin only)."""
    require_admin_token(extract_admin_token(request, authorization))
    _require_plugin(plugin_id)
    if not await plugin_manager.reload_plugin(plugin_id):
        raise HTTPException(status_code=500, detail=f"Failed to reload '{plugin_id}'")
    return {"plugin": plugin_id, "enabled": True}
