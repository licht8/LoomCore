"""Read-only curated host/plugin overview aggregate.

Each section calls one fixed, read-only tool inside a guard so a missing or
failing tool turns that section ``null`` instead of failing the whole request.
Sections run concurrently and are time-bounded, and an unhealthy plugin is never
dispatched, so a broken plugin can neither hang the poll nor trigger a restart
loop (the background healthcheck loop owns restarts).
"""

import asyncio
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

# Upper bound per section so one hung plugin cannot hold the whole request.
OVERVIEW_TOOL_TIMEOUT_SECONDS = 5.0


async def _run_tool(tool_name: str) -> dict | None:
    """Dispatch one section's tool; return ``None`` when it is missing/fails."""
    if not tool_name.startswith("core_"):
        # This endpoint is polled every 15s: dispatching through a dead plugin
        # would add restart-backoff latency and churn inside the request.
        plugin = plugin_manager.plugin_for_tool(tool_name)
        if not plugin or not plugin_manager.is_healthy(plugin):
            return None
    try:
        if tool_name.startswith("core_"):
            coro = dispatch_core_tool(tool_name)
        else:
            coro = plugin_manager.execute_tool_by_name(tool_name)
        return await asyncio.wait_for(coro, OVERVIEW_TOOL_TIMEOUT_SECONDS)
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
    core, disk, metrics, failed_units, wireguard = await asyncio.gather(
        _run_tool(_SECTION_TOOLS["core"]),
        _run_tool(_SECTION_TOOLS["disk"]),
        _run_tool(_SECTION_TOOLS["metrics"]),
        _run_tool(_SECTION_TOOLS["failed_units"]),
        _run_tool(_SECTION_TOOLS["wireguard"]),
    )
    plugins = [
        plugin_manager.get_plugin_status(name)
        for name in plugin_manager.plugin_names()
    ]
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
