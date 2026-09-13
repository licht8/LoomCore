"""MCP tool handlers for the LoomOps plugin."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from .ops import commands, files, processes, services, system_info
from .results import text_result
from .schemas import TOOLS_SCHEMA  # noqa: F401  (re-exported for the server and tests)


def _tool(func: Callable[[dict[str, Any]], dict[str, Any]]):
    @wraps(func)
    def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        data = func(arguments or {})
        output = data.pop("output", "")
        return text_result(output, **data)

    return handler


HANDLERS = {
    "ops_run_command": _tool(commands.run_command),
    "ops_read_file": _tool(files.read_file),
    "ops_write_file": _tool(files.write_file),
    "ops_list_processes": _tool(processes.list_processes),
    "ops_kill_process": _tool(processes.kill_process),
    "ops_systemctl_status": _tool(services.systemctl_status),
    "ops_systemctl_restart": _tool(services.systemctl_restart),
    "ops_disk_usage": _tool(system_info.disk_usage),
    "ops_network_status": _tool(system_info.network_status),
}
