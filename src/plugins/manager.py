"""Plugin Manager — launches plugins as subprocesses + MCP transport (stdin/stdout).

Each plugin runs as a separate process. Communication uses JSON-RPC 2.0 over
stdin/stdout. Call timeout: 30 seconds. A plugin crash does not take down the Hub.
"""

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import time

logger = logging.getLogger("loomcore.plugins")


class PluginState(StrEnum):
    """Plugin process state."""

    IDLE = "idle"
    BUSY = "busy"
    STOPPED = "stopped"


@dataclass
class ManagedPlugin:
    """Runtime record for a single launched plugin subprocess."""

    name: str
    command: list[str] = field(default_factory=list)
    working_dir: str | None = None
    proc: subprocess.Popen | None = None
    state: PluginState = PluginState.IDLE
    restart_attempt: int = 0
    max_restarts: int = 3
    restart_delay_base: float = 1.0
    restart_delay_max: float = 30.0
    tools: list[dict] = field(default_factory=list)


class PluginManager:
    """Manage plugins: launch, MCP transport, tool invocation."""

    def __init__(self):
        self._plugins: dict[str, ManagedPlugin] = {}
        self._tool_plugins: dict[str, str] = {}
        # Tools marked "internal": True (available only to the core, not the LLM)
        self._internal_tools: dict[str, str] = {}

        # Tool call limit: tool_name -> [timestamps]
        self._tool_calls: dict[str, list[float]] = defaultdict(list)
        self._max_tool_calls_per_minute = 10

    def _spawn(self, command: list[str], working_dir: str | None) -> subprocess.Popen:
        """Launch a plugin subprocess with the standard stdio pipes."""
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
        )

    def register(
        self,
        name: str,
        command: list[str],
        working_dir: str | None = None,
    ):
        """Register and launch a plugin as a subprocess.

        Args:
            name: Plugin name (unique identifier).
            command: Launch command [python, path_to_main.py].
            working_dir: Plugin working directory.
        """
        proc = self._spawn(command, working_dir)
        self._plugins[name] = ManagedPlugin(
            name=name,
            command=command,
            working_dir=working_dir,
            proc=proc,
        )
        logger.info(f"Plugin '{name}' started (PID: {proc.pid})")

    def discover_plugins(self, plugins_dir: str = "plugins") -> list[str]:
        """Launch every plugin that has a main.py in a child directory."""
        discovered: list[str] = []
        for plugin_dir in sorted(Path(plugins_dir).iterdir()):
            if not plugin_dir.is_dir() or not (plugin_dir / "main.py").is_file():
                continue
            name = plugin_dir.name
            if name in self._plugins:
                continue
            self.register(name, [sys.executable, str(plugin_dir / "main.py")])
            discovered.append(name)
        return discovered

    def plugin_names(self) -> list[str]:
        return list(self._plugins)

    # ------------------------------------------------------------------
    # Process state helpers
    # ------------------------------------------------------------------

    def _mark_stopped_if_dead(self, plugin: ManagedPlugin, *, detail: str = "") -> bool:
        """Mark a plugin STOPPED if its subprocess has exited.

        Returns True when the process is dead (or already STOPPED), so callers
        can react uniformly instead of repeating the same poll() check.
        """
        if plugin.proc is not None and plugin.proc.poll() is not None:
            if plugin.state != PluginState.STOPPED:
                suffix = f" {detail}" if detail else ""
                logger.warning(f"Plugin '{plugin.name}' process is dead{suffix}; marking STOPPED")
                plugin.state = PluginState.STOPPED
            return True
        return False

    # ------------------------------------------------------------------
    # Task 1: Plugin auto-restart
    # ------------------------------------------------------------------

    async def restart_plugin(self, plugin_name: str) -> bool:
        """Public wrapper for auto-restart (used by the background healthcheck)."""
        return await self._restart_plugin(plugin_name)

    async def _restart_plugin(self, plugin_name: str) -> bool:
        """Restart a plugin with exponential backoff.

        Returns:
            True — if the restart succeeded and the plugin is IDLE.
            False — if attempts are exhausted or an error occurred.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            logger.error(f"Plugin '{plugin_name}' is not registered; cannot restart")
            return False

        # Check the restart limit
        if plugin.restart_attempt >= plugin.max_restarts:
            logger.error(
                f"Plugin '{plugin_name}' exhausted restart attempts "
                f"({plugin.restart_attempt}/{plugin.max_restarts})"
            )
            plugin.state = PluginState.STOPPED
            return False

        plugin.restart_attempt += 1
        attempt = plugin.restart_attempt
        delay = min(
            plugin.restart_delay_base * (2 ** (attempt - 1)), plugin.restart_delay_max
        )

        logger.info(
            f"Restarting plugin '{plugin_name}' ({attempt}/{plugin.max_restarts}), "
            f"delay {delay:.1f}s"
        )

        # Wait before restarting (non-blocking)
        await asyncio.sleep(delay)

        try:
            # Kill the old process if it is still alive
            old_proc = plugin.proc
            if old_proc and old_proc.poll() is None:
                old_proc.terminate()
                try:
                    old_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    old_proc.kill()
                    old_proc.wait()

            # Launch the subprocess again in place
            self._do_register_inplace(plugin_name, plugin.command, plugin.working_dir)

            # Wait for initialize
            await self._initialize_plugin(plugin_name)

            # Fetch tools via tools/list
            tools = await self.get_tools(plugin_name)

            # Refresh the tool cache
            self._rebuild_tool_cache(plugin_name, tools)

            plugin.state = PluginState.IDLE
            logger.info(f"Plugin '{plugin_name}' restarted successfully")
            return True

        except Exception as exc:
            logger.error(f"Failed to restart plugin '{plugin_name}': {exc}")
            plugin.state = PluginState.STOPPED
            return False

    def _do_register_inplace(self, name: str, command: list[str], working_dir: str | None):
        """Launch the subprocess again, keeping the same ManagedPlugin record."""
        proc = self._spawn(command, working_dir)
        plugin = self._plugins[name]
        plugin.proc = proc
        plugin.state = PluginState.IDLE
        logger.info(f"Plugin '{name}' subprocess restarted (PID: {proc.pid})")

    async def _initialize_plugin(self, plugin_name: str) -> None:
        """Send {"method": "initialize"} and wait for the response (10s timeout)."""
        request = {"jsonrpc": "2.0", "method": "initialize"}
        line = json.dumps(request, ensure_ascii=False) + "\n"

        plugin = self._plugins[plugin_name]
        plugin.proc.stdin.write(line)
        plugin.proc.stdin.flush()

        # Wait for the response for up to 10 seconds
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=10,
            )
            logger.info(f"Plugin '{plugin_name}' initialized")
        except TimeoutError:
            logger.warning(f"Plugin '{plugin_name}' initialization timed out (10s)")
            raise

    def _index_tools(self, plugin_name: str, tools: list[dict]) -> list[dict]:
        """Cache a plugin's tools; return the LLM-facing (non-internal) tools."""
        external: list[dict] = []
        for tool in tools:
            tool_name = tool.get("name")
            if not tool_name:
                continue
            if tool.get("internal"):
                self._internal_tools[tool_name] = plugin_name
            else:
                self._tool_plugins[tool_name] = plugin_name
                external.append(tool)
        return external

    def _rebuild_tool_cache(self, plugin_name: str, tools: list[dict]) -> None:
        """Rebuild the plugin's tool caches (drop old entries, add new ones)."""
        # Remove this plugin's old entries
        for cache in (self._tool_plugins, self._internal_tools):
            stale = [name for name, owner in cache.items() if owner == plugin_name]
            for name in stale:
                del cache[name]

        # Add new ones: internal — core only, the rest — for the LLM
        self._index_tools(plugin_name, tools)

    # ------------------------------------------------------------------
    # Task 2: is_healthy / get_plugin_status methods
    # ------------------------------------------------------------------

    def is_healthy(self, plugin_name: str) -> bool:
        """Check whether a plugin is healthy (process alive and not STOPPED).

        Args:
            plugin_name: Plugin name.

        Returns:
            True — the plugin is alive and not stopped.
            False — the plugin is missing, dead, or stopped.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin or plugin.state == PluginState.STOPPED:
            return False
        return not self._mark_stopped_if_dead(plugin)

    def get_plugin_status(self, plugin_name: str) -> dict:
        """Return plugin status in the API format.

        Returns:
            {"name": ..., "state": ..., "pid": ..., "restart_attempt": ..., "is_healthy": ...}
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return {
                "name": plugin_name,
                "state": "stopped",
                "pid": None,
                "restart_attempt": 0,
                "is_healthy": False,
            }

        pid = plugin.proc.pid
        healthy = self.is_healthy(plugin_name)
        return {
            "name": plugin_name,
            "state": plugin.state.value,
            "pid": pid,
            "restart_attempt": plugin.restart_attempt,
            "is_healthy": healthy,
        }

    # ------------------------------------------------------------------
    # Task 5: Handle BrokenPipeError / OSError in _write_request
    # ------------------------------------------------------------------

    async def _write_request(self, plugin_name: str, request: dict) -> str:
        """Write a JSON-RPC request to the plugin's stdin. Return the request_id.

        If the process is dead or the input stream is broken (BrokenPipeError/OSError):
        1. Mark STOPPED and call _restart_plugin().
        2. After a successful restart, retry the write once.
        3. If the restart failed, raise RuntimeError.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' is not registered")

        if plugin.state == PluginState.BUSY:
            raise RuntimeError(f"Plugin '{plugin_name}' is busy (30s timeout)")

        # Check whether the process is dead
        if self._mark_stopped_if_dead(plugin):
            raise RuntimeError(f"Plugin '{plugin_name}' is unavailable (dead process)")

        request["id"] = request.get("id") or str(uuid.uuid4())
        line = json.dumps(request, ensure_ascii=False)

        try:
            plugin.proc.stdin.write(line + "\n")
            plugin.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.error(f"BrokenPipeError for plugin '{plugin_name}' on write: {exc}")
            plugin.state = PluginState.STOPPED

            # Try to restart
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' from _write_request")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Plugin '{plugin_name}' is unavailable (restart failed)") from exc

            # Retry the write once
            plugin = self._plugins[plugin_name]
            logger.info(f"Retrying write after restarting plugin '{plugin_name}'")
            try:
                plugin.proc.stdin.write(line + "\n")
                plugin.proc.stdin.flush()
            except (BrokenPipeError, OSError) as retry_exc:
                logger.error(f"BrokenPipeError on retry for plugin '{plugin_name}': {retry_exc}")
                raise RuntimeError(f"Plugin '{plugin_name}' is unavailable (broken input stream)") from retry_exc

        return request["id"]

    def _read_response(self, plugin_name: str) -> dict:
        """Read the next JSON-RPC response, skipping MCP notifications."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' is not registered")

        while True:
            line = plugin.proc.stdout.readline()
            if not line:
                logger.error(f"Plugin '{plugin_name}' terminated")
                plugin.state = PluginState.STOPPED
                raise RuntimeError(f"Plugin '{plugin_name}' terminated")

            response = json.loads(line.strip())
            if "id" in response:
                return response

    async def get_tools(self, plugin_name: str) -> list[dict]:
        """Fetch a plugin's tools (tools/list → JSON Schema).

        On a dead process or STOPPED state, attempts auto-restart.
        """
        plugin = self._plugins.get(plugin_name)

        if not plugin or self._mark_stopped_if_dead(plugin) or plugin.state == PluginState.STOPPED:
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' before get_tools")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Plugin '{plugin_name}' is unavailable")
            plugin = self._plugins[plugin_name]

        # Check again — in case the restart failed
        if self._mark_stopped_if_dead(plugin) or plugin.state == PluginState.STOPPED:
            raise RuntimeError(f"Plugin '{plugin_name}' is unavailable (dead process)")

        try:
            request = {"jsonrpc": "2.0", "method": "tools/list"}
            await self._write_request(plugin_name, request)
            plugin.state = PluginState.BUSY
            response = await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=30,
            )
            result = response.get("result", {})
            return result.get("tools", [])
        finally:
            if plugin.state == PluginState.BUSY:
                plugin.state = PluginState.IDLE

    async def get_all_tools(self) -> list[dict]:
        """Fetch tools from all registered plugins.

        On failure to fetch a plugin's tools, attempts auto-restart.
        Returns the tools that were fetched successfully.
        """
        all_tools: list[dict] = []
        failed_plugins: list[str] = []
        for plugin_name in self.plugin_names():
            try:
                tools = await self.get_tools(plugin_name)
                all_tools.extend(self._index_tools(plugin_name, tools))
            except Exception as exc:
                logger.warning(f"Failed to fetch tools for plugin '{plugin_name}': {exc}")
                failed_plugins.append(plugin_name)

        # Try to restart the plugins that did not respond
        for plugin_name in failed_plugins:
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' from get_all_tools")
            try:
                if await self._restart_plugin(plugin_name):
                    # _restart_plugin already refreshed the tool cache; re-read the
                    # tools only to include them in the returned list.
                    tools = await self.get_tools(plugin_name)
                    all_tools.extend(self._index_tools(plugin_name, tools))
            except Exception as inner_exc:
                logger.error(f"Failed to restart plugin '{plugin_name}' from get_all_tools: {inner_exc}")

        return all_tools

    def plugin_for_tool(self, tool_name: str) -> str:
        plugin_name = self._tool_plugins.get(tool_name)
        if not plugin_name:
            raise ValueError(f"Tool '{tool_name}' is not registered")
        return plugin_name

    async def execute_internal_tool(
        self, tool_name: str, arguments: dict | None = None
    ) -> dict:
        """Invoke a plugin's internal tool (not exposed to the LLM).

        Used by the core for service operations (for example, serving artifacts).
        """
        plugin_name = self._internal_tools.get(tool_name)
        if not plugin_name:
            # The cache may not be populated yet — query the plugins once
            await self.get_all_tools()
            plugin_name = self._internal_tools.get(tool_name)
        if not plugin_name:
            raise ValueError(f"Internal tool '{tool_name}' is not registered")
        return await self.execute_tool(plugin_name, tool_name, arguments)

    # ------------------------------------------------------------------
    # Task 1 (continued): execute_tool with dead-process detection
    # ------------------------------------------------------------------

    async def execute_tool(
        self,
        plugin_name: str,
        tool_name: str,
        arguments: dict | None = None,
    ) -> dict:
        """Invoke a plugin tool (tools/call → result).

        Timeout: 30 seconds. If the plugin does not respond, kill the process.
        On STOPPED or a dead process, attempts auto-restart.
        """
        plugin = self._plugins.get(plugin_name)

        if not plugin or self._mark_stopped_if_dead(
            plugin, detail=f"while calling '{tool_name}'"
        ) or plugin.state == PluginState.STOPPED:
            # Try auto-restart
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' before calling '{tool_name}'")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Plugin '{plugin_name}' is unavailable")

            # After a successful restart — retry the call
            plugin = self._plugins[plugin_name]

        # --- Tool call limit (protects against AI loops); not for internal tools ---
        if tool_name not in self._internal_tools:
            now = time()
            window = 60  # seconds
            self._tool_calls[tool_name] = [
                t for t in self._tool_calls[tool_name] if t > now - window
            ]
            if len(self._tool_calls[tool_name]) >= self._max_tool_calls_per_minute:
                raise RuntimeError(
                    f"Too many calls to '{tool_name}' — limit "
                    f"{self._max_tool_calls_per_minute}/min"
                )
            self._tool_calls[tool_name].append(now)

        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        try:
            await self._write_request(plugin_name, request)
            plugin.state = PluginState.BUSY
            response = await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=30,
            )
            if response.get("error"):
                raise RuntimeError(response["error"].get("message", "MCP plugin error"))
            result = response.get("result")
            if result is None:
                raise RuntimeError(f"Plugin '{plugin_name}' returned no result")
            return result
        except asyncio.CancelledError:
            # The caller cancelled mid-call; the stdio stream can no longer be
            # trusted to pair requests with responses, so restart the plugin.
            logger.warning(
                f"Call to '{tool_name}' in '{plugin_name}' cancelled — restarting plugin"
            )
            self._kill_and_stop(plugin_name)
            raise
        except TimeoutError:
            logger.warning(f"30s timeout for plugin '{plugin_name}' — killing process")
            plugin.proc.kill()
            plugin.state = PluginState.STOPPED
            raise TimeoutError(
                f"Call to '{tool_name}' in '{plugin_name}' timed out (30s)"
            ) from None

        finally:
            if plugin.state == PluginState.BUSY:
                plugin.state = PluginState.IDLE

    def _kill_and_stop(self, plugin_name: str) -> None:
        """Kill a plugin subprocess and mark it STOPPED for the next auto-restart.

        Used when an in-flight tool call is cancelled: the plugin's stdio stream
        may still hold the abandoned request/response, so the process is replaced
        on the next call instead of risking a request/response desync.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return
        proc = plugin.proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                logger.exception(f"Failed to kill plugin '{plugin_name}'")
        plugin.state = PluginState.STOPPED

    async def execute_tool_by_name(
        self, tool_name: str, arguments: dict | None = None
    ) -> dict:
        return await self.execute_tool(
            self.plugin_for_tool(tool_name), tool_name, arguments
        )
