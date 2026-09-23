"""Core meta-tools exposed to the assistant and the REST API.

These tools describe and steer the plugin platform itself (health, plugin
lifecycle, capabilities). They intentionally live in ``src/core`` and return the
same MCP-shaped result as plugin tools so the WebSocket layer needs no special
casing. Mutating tools are approval-gated via ``classify_tool_risk``.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Awaitable, Callable

from src.core.contracts import validate_tool_args
from src.core.security import classify_tool_risk
from src.plugins import plugin_manager
from src.plugins.errors import PluginPolicyDenied
from src.plugins.manifest import (
    CAPABILITIES,
    effective_capabilities,
    host_network_looks_active,
)

logger = logging.getLogger("loomcore.core_tools")

_STARTED_AT = time.monotonic()

_PLUGIN_ARG_SCHEMA = {
    "type": "object",
    "properties": {
        "plugin": {"type": "string", "description": "Plugin id (folder name)."}
    },
    "required": ["plugin"],
}
_OPTIONAL_PLUGIN_SCHEMA = {
    "type": "object",
    "properties": {
        "plugin": {
            "type": "string",
            "description": (
                "Optional plugin id (e.g. LoomOps) or `core`; omit to list everything."
            ),
        }
    },
    "required": [],
}
_NO_ARGS_SCHEMA = {"type": "object", "properties": {}, "required": []}

# Plugin-id aliases that mean "the core's own meta-tools".
_CORE_PLUGIN_ALIASES = frozenset({"core", "loomcore", "loom_core"})


CORE_TOOLS_SCHEMA: list[dict] = [
    {
        "name": "core_health",
        "description": (
            "Report LoomCore health (uptime and plugin counts). Safe to call."
        ),
        "inputSchema": _NO_ARGS_SCHEMA,
        "risk": "safe",
    },
    {
        "name": "core_list_plugins",
        "description": (
            "List every discovered plugin with its version, enabled state and health."
        ),
        "inputSchema": _NO_ARGS_SCHEMA,
        "risk": "safe",
    },
    {
        "name": "core_plugin_status",
        "description": "Return detailed status, version and warnings for a single plugin.",
        "inputSchema": _PLUGIN_ARG_SCHEMA,
        "risk": "safe",
    },
    {
        "name": "core_list_tools",
        "description": (
            "List the tools exposed by the core and by enabled plugins. Pass an "
            "optional plugin id (or `core`) to list only that source."
        ),
        "inputSchema": _OPTIONAL_PLUGIN_SCHEMA,
        "risk": "safe",
    },
    {
        "name": "core_capabilities",
        "description": (
            "Report the core runtime capabilities and the declared plugin permissions."
        ),
        "inputSchema": _NO_ARGS_SCHEMA,
        "risk": "safe",
    },
    {
        "name": "core_enable_plugin",
        "description": (
            "Enable a disabled plugin, start it and persist the change. Requires approval."
        ),
        "inputSchema": _PLUGIN_ARG_SCHEMA,
        "risk": "dangerous",
    },
    {
        "name": "core_disable_plugin",
        "description": (
            "Stop a plugin and persist its disabled state. Requires approval."
        ),
        "inputSchema": _PLUGIN_ARG_SCHEMA,
        "risk": "dangerous",
    },
    {
        "name": "core_reload_plugin",
        "description": (
            "Restart an enabled plugin and refresh its tools. Requires approval."
        ),
        "inputSchema": _PLUGIN_ARG_SCHEMA,
        "risk": "dangerous",
    },
]

CORE_TOOL_NAMES = frozenset(tool["name"] for tool in CORE_TOOLS_SCHEMA)

# Risk declared alongside each core tool's schema.
CORE_TOOL_RISK = {tool["name"]: tool.get("risk") for tool in CORE_TOOLS_SCHEMA}

# Input schema declared alongside each core tool, keyed by name.
CORE_TOOL_SCHEMAS = {
    tool["name"]: tool.get("inputSchema") or {} for tool in CORE_TOOLS_SCHEMA
}


def resolve_tool_risk(tool_name: str) -> str:
    """Resolve a tool's risk: core declaration, then plugin, then heuristic."""
    declared = CORE_TOOL_RISK.get(tool_name) or plugin_manager.tool_risk(tool_name)
    return classify_tool_risk(tool_name, declared)


def _result(text: str, **data) -> dict:
    return {"content": [{"type": "text", "text": text}], "data": data}


def _plugin_name(arguments: dict) -> str:
    value = (
        arguments.get("plugin")
        or arguments.get("plugin_id")
        or arguments.get("name")
        or arguments.get("id")
    )
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Argument 'plugin' is required")
    return value.strip()


def _optional_plugin_name(arguments: dict) -> str:
    """Return the requested plugin filter, or ``""`` when none was given."""
    value = (
        arguments.get("plugin")
        or arguments.get("plugin_id")
        or arguments.get("name")
        or arguments.get("id")
    )
    return value.strip() if isinstance(value, str) else ""


def _require_plugin(arguments: dict) -> str:
    name = _plugin_name(arguments)
    if name not in plugin_manager.plugin_names():
        raise ValueError(f"Plugin '{name}' is not registered")
    return name


async def _core_health(arguments: dict) -> dict:
    names = plugin_manager.plugin_names()
    enabled = [name for name in names if plugin_manager.is_enabled(name)]
    healthy = [name for name in enabled if plugin_manager.is_healthy(name)]
    data = {
        "status": "ok",
        "uptime_seconds": round(time.monotonic() - _STARTED_AT, 3),
        "plugin_counts": {
            "total": len(names),
            "enabled": len(enabled),
            "healthy": len(healthy),
        },
    }
    text = (
        f"Core is healthy. {len(enabled)}/{len(names)} plugins enabled, "
        f"{len(healthy)} healthy."
    )
    text += f"\n\n**Plugins enabled** ({len(enabled)}):\n"
    text += "\n".join(f"- {name}" for name in enabled) if enabled else "- none"
    return _result(text, **data)


async def _core_list_plugins(arguments: dict) -> dict:
    plugins = []
    for name in plugin_manager.plugin_names():
        status = plugin_manager.get_plugin_status(name)
        plugins.append(
            {
                "id": name,
                "version": plugin_manager.plugin_version(name),
                "enabled": plugin_manager.is_enabled(name),
                "state": status["state"],
                "healthy": status["is_healthy"],
            }
        )
    text_lines = [f"{len(plugins)} plugins discovered:"]
    for plugin in plugins:
        version = plugin["version"] or "unknown"
        if not plugin["enabled"]:
            condition = "disabled"
        elif plugin["healthy"]:
            condition = "enabled, healthy"
        else:
            condition = "enabled, unhealthy"
        text_lines.append(
            f"- {plugin['id']} v{version} — {condition} ({plugin['state']})"
        )
    text = "\n".join(text_lines)
    return _result(text, plugins=plugins)


async def _core_plugin_status(arguments: dict) -> dict:
    name = _require_plugin(arguments)
    status = plugin_manager.get_plugin_status(name)
    warnings = plugin_manager.permission_warnings(name)
    data = {
        "plugin": name,
        "version": plugin_manager.plugin_version(name),
        "enabled": plugin_manager.is_enabled(name),
        "warnings": warnings,
        **status,
    }
    text = (
        f"Plugin '{name}': state={status['state']}, "
        f"enabled={data['enabled']}, healthy={status['is_healthy']}."
    )
    if warnings:
        text += "\n\n**Warnings:**\n" + "\n".join(
            f"- {warning}" for warning in warnings
        )
    return _result(text, **data)


async def _core_list_tools(arguments: dict) -> dict:
    requested = _optional_plugin_name(arguments)
    core_tools = [
        {"name": tool["name"], "description": tool["description"]}
        for tool in CORE_TOOLS_SCHEMA
    ]
    core_line = (
        f"**Core tools** ({len(core_tools)}):\n"
        + "\n".join(f"- `{tool['name']}`" for tool in core_tools)
    )

    # The core's own meta-tools: no plugin round trip is needed.
    if requested and requested.lower() in _CORE_PLUGIN_ALIASES:
        return _result(core_line, core_tools=core_tools, plugin_tools=[])

    if requested:
        if requested not in plugin_manager.plugin_names():
            raise ValueError(f"Plugin '{requested}' is not registered")
        plugin_names = [requested]
        lines: list[str] = []
    else:
        plugin_names = plugin_manager.plugin_names()
        lines = [core_line]

    plugin_tools: list[dict] = []
    for name in plugin_names:
        if not plugin_manager.is_enabled(name):
            lines.append(f"- {name} (disabled)")
            continue
        try:
            if plugin_manager.tools_indexed():
                # Served from the indexed catalog: no JSON-RPC round trip.
                names = [
                    tool["name"]
                    for tool in plugin_manager.get_cached_tools()
                    if isinstance(tool.get("name"), str)
                    and plugin_manager.plugin_for_tool(tool["name"]) == name
                ]
            else:
                tools = await plugin_manager.get_tools(name)
                if not isinstance(tools, list):
                    raise ValueError(
                        f"plugin '{name}' returned a non-list tool catalog"
                    )
                names = [
                    tool["name"]
                    for tool in tools
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                ]
        except Exception as exc:  # noqa: BLE001 - best-effort catalog
            logger.warning(f"Cannot list tools of plugin '{name}': {exc}")
            lines.append(f"- {name} (unavailable)")
            continue
        plugin_tools.extend(
            {"name": tool_name, "plugin": name} for tool_name in names
        )
        lines.append(
            f"**{name}** ({len(names)}):\n"
            + "\n".join(f"- `{tool_name}`" for tool_name in names)
        )
    text = "\n".join(lines)
    return _result(text, core_tools=core_tools, plugin_tools=plugin_tools)


async def _core_capabilities(arguments: dict) -> dict:
    caps = effective_capabilities()
    host_networked = host_network_looks_active()
    reports = []
    for name in plugin_manager.plugin_names():
        manifest = plugin_manager.plugin_manifest(name) or {}
        reports.append(
            {
                "id": name,
                "permissions": manifest.get("permissions", {}),
                "warnings": plugin_manager.permission_warnings(name),
            }
        )
    data = {
        "capabilities": {
            name: (bool(caps & (1 << bit)) if caps is not None else None)
            for name, bit in CAPABILITIES.items()
        },
        "hostname": socket.gethostname(),
        "host_networked": host_networked,
        "plugins": reports,
    }
    text = (
        "Host networking is active." if host_networked else
        "Container is bridge-networked (host capabilities may be limited)."
    )
    return _result(text, **data)


async def _core_enable_plugin(arguments: dict) -> dict:
    name = _require_plugin(arguments)
    try:
        enabled = await plugin_manager.enable_plugin(name)
    except PluginPolicyDenied as exc:
        raise RuntimeError(f"Plugin '{name}' is not permitted by policy") from exc
    if not enabled:
        raise RuntimeError(f"Failed to enable plugin '{name}'")
    return _result(f"Plugin '{name}' enabled.", plugin=name, enabled=True)


async def _core_disable_plugin(arguments: dict) -> dict:
    name = _require_plugin(arguments)
    if not await plugin_manager.disable_plugin(name):
        raise RuntimeError(f"Failed to disable plugin '{name}'")
    return _result(f"Plugin '{name}' disabled.", plugin=name, enabled=False)


async def _core_reload_plugin(arguments: dict) -> dict:
    name = _require_plugin(arguments)
    if not await plugin_manager.reload_plugin(name):
        raise RuntimeError(f"Failed to reload plugin '{name}'")
    return _result(f"Plugin '{name}' reloaded.", plugin=name, enabled=True)


_HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "core_health": _core_health,
    "core_list_plugins": _core_list_plugins,
    "core_plugin_status": _core_plugin_status,
    "core_list_tools": _core_list_tools,
    "core_capabilities": _core_capabilities,
    "core_enable_plugin": _core_enable_plugin,
    "core_disable_plugin": _core_disable_plugin,
    "core_reload_plugin": _core_reload_plugin,
}


async def dispatch_core_tool(
    name: str, arguments: dict | None = None, *, approved: bool = False
) -> dict:
    """Execute a core meta-tool and return an MCP-shaped result.

    A dangerous tool is refused unless the caller passes ``approved=True``.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown core tool '{name}'")
    if resolve_tool_risk(name) == "dangerous" and not approved:
        raise PermissionError(f"Core tool '{name}' requires approval")
    validate_tool_args(name, arguments, CORE_TOOL_SCHEMAS.get(name))
    return await handler(arguments or {})
