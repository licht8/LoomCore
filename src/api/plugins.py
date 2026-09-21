"""Routes for plugin and tool status."""

import contextlib
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.deps import extract_admin_token
from src.core.audit import record_mutation_isolated
from src.core.core_tools import CORE_TOOLS_SCHEMA
from src.core.rate_limit import limiter
from src.core.security import require_admin_token
from src.plugins import plugin_manager

router = APIRouter(prefix="/api", tags=["plugins"])

logger = logging.getLogger("loomcore.api.plugins")


@router.get("/plugins/status")
@limiter.limit("60 per 60 seconds")
async def get_plugins_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the status of all plugins."""
    require_admin_token(extract_admin_token(request, authorization), request)
    return {
        "plugins": {
            name: plugin_manager.get_plugin_status(name)
            for name in plugin_manager.plugin_names()
        }
    }


@router.get("/tools")
@limiter.limit("60 per 60 seconds")
async def list_plugins_tools(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the tool schemas of all plugins (for Swagger/UI)."""
    require_admin_token(extract_admin_token(request, authorization), request)
    try:
        tools = list(CORE_TOOLS_SCHEMA) + await plugin_manager.get_all_tools()
    except Exception as exc:
        logger.exception("Failed to collect plugin tool schemas")
        raise HTTPException(
            status_code=500, detail="Failed to collect tool schemas"
        ) from exc
    return {"tools": tools}


def _require_plugin(plugin_id: str) -> None:
    if plugin_id not in plugin_manager.plugin_names():
        raise HTTPException(
            status_code=404, detail=f"Plugin '{plugin_id}' is not registered"
        )


async def _audit_plugin_action(action: str, plugin_id: str, ok: bool) -> None:
    """Record a plugin-lifecycle mutation in the central audit log."""
    with contextlib.suppress(Exception):
        await record_mutation_isolated(
            tool=f"core_{action}_plugin",
            arguments={"plugin": plugin_id},
            risk="dangerous",
            status="success" if ok else "failed",
            error=None if ok else f"Failed to {action} '{plugin_id}'",
        )


@router.post("/plugins/{plugin_id}/enable")
@limiter.limit("10 per 60 seconds")
async def enable_plugin(
    plugin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Enable and start a disabled plugin (admin only)."""
    require_admin_token(extract_admin_token(request, authorization), request)
    _require_plugin(plugin_id)
    enabled = await plugin_manager.enable_plugin(plugin_id)
    await _audit_plugin_action("enable", plugin_id, enabled)
    if not enabled:
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
    require_admin_token(extract_admin_token(request, authorization), request)
    _require_plugin(plugin_id)
    disabled = await plugin_manager.disable_plugin(plugin_id)
    await _audit_plugin_action("disable", plugin_id, disabled)
    if not disabled:
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
    require_admin_token(extract_admin_token(request, authorization), request)
    _require_plugin(plugin_id)
    reloaded = await plugin_manager.reload_plugin(plugin_id)
    await _audit_plugin_action("reload", plugin_id, reloaded)
    if not reloaded:
        raise HTTPException(status_code=500, detail=f"Failed to reload '{plugin_id}'")
    return {"plugin": plugin_id, "enabled": True}
