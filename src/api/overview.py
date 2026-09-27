"""Read-only curated host/plugin overview aggregate.

Each section calls one fixed, read-only tool inside a guard so a missing or
failing tool turns that section ``null`` instead of failing the whole request.
"""

import logging

from fastapi import APIRouter, Header, Request

from src.api.deps import require_admin_token_from_request
from src.core.core_tools import dispatch_core_tool
from src.core.rate_limit import limiter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.api.overview")

router = APIRouter(prefix="/api", tags=["overview"])

# Fixed, curated, read-only tool per non-plugin section. Keep this list small.
_SECTION_TOOLS = {
    "core": "core_health",
    "disk": "ops_disk_usage",
    "metrics": "ops_system_metrics",
    "failed_units": "ops_failed_units",
    "wireguard": "wg_list_interfaces",
}


async def _run_tool(tool_name: str) -> dict | None:
    """Dispatch one section's tool; return ``None`` when it is missing/fails."""
    try:
        if tool_name.startswith("core_"):
            return await dispatch_core_tool(tool_name)
        return await plugin_manager.execute_tool_by_name(tool_name)
    except Exception as exc:
        logger.warning("overview: tool %s unavailable: %s", tool_name, exc)
        return None


@router.get("/overview")
@limiter.limit("60 per 60 seconds")
async def get_overview(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the curated host/plugin overview; every section may be null."""
    require_admin_token_from_request(request, authorization)
    core = await _run_tool(_SECTION_TOOLS["core"])
    plugins = [
        plugin_manager.get_plugin_status(name)
        for name in plugin_manager.plugin_names()
    ]
    disk = await _run_tool(_SECTION_TOOLS["disk"])
    metrics = await _run_tool(_SECTION_TOOLS["metrics"])
    failed_units = await _run_tool(_SECTION_TOOLS["failed_units"])
    wireguard = await _run_tool(_SECTION_TOOLS["wireguard"])
    return {
        "core": core,
        "plugins": plugins,
        "host": {
            "disk": disk,
            "metrics": metrics,
            "failed_units": failed_units,
        },
        "wireguard": wireguard,
    }
