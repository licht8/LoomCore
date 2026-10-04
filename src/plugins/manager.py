"""Plugin Manager — launches plugins as subprocesses + MCP transport (stdin/stdout).

Each plugin runs as a separate process. Communication uses JSON-RPC 2.0 over
stdin/stdout. Call timeout: a per-tool value declared by the plugin, defaulting
to 30 seconds. A plugin crash does not take down the Hub.
"""

import asyncio
import contextlib
import json
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from time import monotonic, time

from src.config import settings
from src.core.contracts import validate_tool_args, validate_tool_result
from src.core.security import classify_tool_risk
from src.core.security_log import log_security_event
from src.core.timing import log_stage
from src.core.version import CORE_VERSION
from src.plugins.errors import PluginPolicyDenied
from src.plugins.manifest import (
    ManifestError,
    check_compatibility,
    check_permissions,
    load_manifest,
    validate_manifest,
)
from src.plugins.state import load_disabled, save_disabled

logger = logging.getLogger("loomcore.plugins")

_VALID_RISKS = frozenset({"safe", "moderate", "dangerous"})

# Per-tool call timeout: plugins may declare an optional ``timeout`` (seconds)
# in their TOOLS_SCHEMA; anything heavier than MAX is clamped so a bad value
# cannot hang a request for an unbounded time.
DEFAULT_TOOL_TIMEOUT = 30.0
MAX_TOOL_TIMEOUT = 600.0


def _normalize_timeout(value: object, tool_name: str) -> float | None:
    """Return a clamped per-tool timeout, or None when absent/malformed.

    A plugin declares an optional ``timeout`` (seconds) per tool; only a
    positive int/float is accepted (bools are rejected), clamped to
    ``MAX_TOOL_TIMEOUT``. Anything else is ignored with a warning so a bad
    declaration falls back to the default instead of breaking discovery.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning(
            f"Tool '{tool_name}' declares an invalid timeout {value!r}; using default"
        )
        return None
    if value <= 0:
        logger.warning(
            f"Tool '{tool_name}' declares a non-positive timeout {value!r}; using default"
        )
        return None
    try:
        number = float(value)
    except OverflowError:
        logger.warning(
            f"Tool '{tool_name}' declares an unrepresentable timeout {value!r}; using default"
        )
        return None
    if not math.isfinite(number):
        logger.warning(
            f"Tool '{tool_name}' declares a non-finite timeout {value!r}; using default"
        )
        return None
    return min(number, MAX_TOOL_TIMEOUT)


def allowed_plugins() -> set[str]:
    """Plugin ids the admin trusts (empty means "no allowlist configured").

    Plugins run as un-sandboxed subprocesses, so an allowlist is the practical
    trust boundary: when ``LOOMCORE_PLUGINS_ALLOWED`` is set, only the listed
    plugin ids may be launched (comma- or whitespace-separated). An empty value
    preserves the previous behaviour of launching every discovered plugin.
    """
    raw = settings.plugins_allowed
    return {
        part
        for part in (chunk.strip() for chunk in raw.replace(",", " ").split())
        if part
    }


def _normalize_risk(value: object) -> str:
    """Normalize a plugin-declared risk, or '' when it is absent/invalid."""
    if isinstance(value, str) and value.strip().lower() in _VALID_RISKS:
        return value.strip().lower()
    return ""

# Leading severity tokens emitted by plugin stderr formatters.
_PLUGIN_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class PluginState(StrEnum):
    """Plugin process state."""

    IDLE = "idle"
    BUSY = "busy"
    STOPPED = "stopped"


class _PluginLock:
    """A per-plugin reentrant async lock.

    ``asyncio.Lock`` is not reentrant, but a broken write triggers an in-place
    restart that re-initializes the plugin through the same transport while the
    caller still holds the lock; tracking the owning task lets those nested
    calls proceed instead of deadlocking.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    async def __aexit__(self, *exc_info: object) -> None:
        self._depth -= 1
        if self._depth <= 0:
            self._owner = None
            self._depth = 0
            self._lock.release()


@dataclass
class ManagedPlugin:
    """Runtime record for a single launched plugin subprocess."""

    name: str
    command: list[str] = field(default_factory=list)
    working_dir: str | None = None
    proc: subprocess.Popen | None = None
    state: PluginState = PluginState.IDLE
    # Serializes the whole write+read exchange on this plugin's stdio stream.
    lock: _PluginLock = field(default_factory=_PluginLock)
    restart_attempt: int = 0
    max_restarts: int = 3
    restart_delay_base: float = 1.0
    restart_delay_max: float = 30.0
    tools: list[dict] = field(default_factory=list)
    enabled: bool = True
    version: str = ""
    manifest: dict | None = None
    permission_warnings: list[str] = field(default_factory=list)


class PluginManager:
    """Manage plugins: launch, MCP transport, tool invocation."""

    def __init__(self, state_path: Path | None = None):
        self._plugins: dict[str, ManagedPlugin] = {}
        self._tool_plugins: dict[str, str] = {}
        # Tools marked "internal": True (available only to the core, not the LLM)
        self._internal_tools: dict[str, str] = {}
        # Per-tool contracts published by the plugin (name -> inputSchema).
        self._tool_schemas: dict[str, dict] = {}
        # Per-tool risk declared by the plugin (name -> safe/moderate/dangerous).
        self._tool_risk: dict[str, str] = {}
        # Per-tool report declaration: "always" for ``report: true``, otherwise
        # the name of the argument whose read-only value makes it a report.
        self._tool_report: dict[str, str] = {}
        # Per-tool call timeout (seconds) declared in TOOLS_SCHEMA; tools absent
        # from this map use DEFAULT_TOOL_TIMEOUT.
        self._tool_timeouts: dict[str, float] = {}
        # True once the external tool catalog has been indexed at least once.
        self._tools_indexed: bool = False

        # Persisted enable/disable state (in-memory only when no path is given)
        self._state_path = state_path
        self._disabled: set[str] = load_disabled(state_path) if state_path else set()

        # Tool call limit: tool_name -> [timestamps]
        self._tool_calls: dict[str, list[float]] = defaultdict(list)
        self._max_tool_calls_per_minute = 10

    def _spawn(self, command: list[str], working_dir: str | None) -> subprocess.Popen:
        """Launch a plugin subprocess in its own session/process group.

        Isolating the child in a new session lets a timed-out call signal the
        whole tree (grandchildren like ``apt-get`` or ``wg-quick`` included)
        instead of orphaning them.
        """
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=working_dir,
            **kwargs,
        )

    def _drain_stderr(self, name: str, proc: subprocess.Popen) -> None:
        """Forward a plugin's stderr to our logs in a daemon thread.

        Without this, plugin logging and tracebacks are invisible and a plugin
        that writes more than a pipe buffer of stderr can block forever.
        """
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        plugin_logger = logging.getLogger(f"loomcore.plugins.{name}")

        def _reader() -> None:
            try:
                for line in stream:
                    message = line.rstrip()
                    if not message:
                        continue
                    # Plugins format lines as "LEVEL logger: message" (see the
                    # plugin's _configure_logging); preserve that severity so
                    # warnings/errors still surface at the core's default level.
                    level = _PLUGIN_LOG_LEVELS.get(
                        message.split(" ", 1)[0], logging.INFO
                    )
                    plugin_logger.log(level, "%s", message)
            except Exception:  # pragma: no cover - a broken pipe on shutdown
                logger.debug("stderr reader for '%s' stopped", name)

        threading.Thread(
            target=_reader,
            name=f"plugin-stderr-{name}",
            daemon=True,
        ).start()

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
        self._drain_stderr(name, proc)
        logger.info(f"Plugin '{name}' started (PID: {proc.pid})")

    def discover_plugins(self, plugins_dir: str = "plugins") -> list[str]:
        """Discover and launch manifest-aware plugins.

        An invalid manifest is logged and the plugin skipped; a missing manifest
        falls back to the folder name as the plugin identity. A plugin is only
        launched when ``default_enabled`` is true, it is not in the persisted
        disabled set, and (when ``LOOMCORE_PLUGINS_ALLOWED`` is set) its id is
        allow-listed.
        """
        discovered: list[str] = []
        trusted = allowed_plugins()
        for plugin_dir in sorted(Path(plugins_dir).iterdir()):
            if not plugin_dir.is_dir() or not (plugin_dir / "main.py").is_file():
                continue
            name = plugin_dir.name
            if name in self._plugins:
                continue

            manifest, warnings = self._resolve_manifest(plugin_dir, name)
            if manifest is None:
                continue

            command = [sys.executable, str(plugin_dir / "main.py")]
            incompatible = list(manifest.get("_compatibility") or [])
            untrusted = bool(trusted) and name not in trusted
            enabled = (
                bool(manifest.get("default_enabled", True))
                and name not in self._disabled
                and not incompatible
                and not untrusted
            )
            if enabled:
                self.register(name, command)
                plugin = self._plugins.get(name)
                if plugin is not None:
                    self._apply_manifest(plugin, manifest, warnings)
            else:
                # Keep a record so the plugin can be enabled later (or so the
                # reason it was not launched stays visible), but do not spawn.
                if incompatible:
                    logger.warning(
                        f"Plugin '{name}' is incompatible with core "
                        f"{CORE_VERSION}; not launching"
                    )
                elif untrusted:
                    log_security_event(
                        "plugin_rejected",
                        plugin=name,
                        reason="not_in_allowlist",
                    )
                    logger.warning(
                        f"Plugin '{name}' is not in LOOMCORE_PLUGINS_ALLOWED; "
                        f"not launching"
                    )
                else:
                    logger.info(f"Plugin '{name}' is disabled; not launching")
                blocked = (
                    ["plugin is not in LOOMCORE_PLUGINS_ALLOWED"] if untrusted else []
                )
                self._plugins[name] = ManagedPlugin(
                    name=name,
                    command=command,
                    state=PluginState.STOPPED,
                    enabled=False,
                    version=str(manifest.get("version") or ""),
                    manifest=manifest,
                    permission_warnings=list(warnings) + incompatible + blocked,
                )
            discovered.append(name)
        return discovered

    def _resolve_manifest(
        self, plugin_dir: Path, name: str
    ) -> tuple[dict | None, list[str]]:
        """Load and validate a plugin manifest.

        Returns ``(manifest, permission_warnings)``; ``(None, [])`` means the
        plugin must be skipped (invalid manifest).
        """
        try:
            manifest = load_manifest(plugin_dir)
        except ManifestError as exc:
            logger.warning(f"Skipping plugin '{name}': {exc}")
            return None, []

        if manifest is None:
            logger.warning(
                f"Plugin '{name}' has no manifest.json; using folder-name identity"
            )
            return {"id": name, "name": name, "default_enabled": True}, []

        issues = validate_manifest(manifest, expected_id=name)
        if issues:
            logger.warning(
                f"Skipping plugin '{name}': manifest issues: {'; '.join(issues)}"
            )
            return None, []

        warnings = check_permissions(manifest)
        for warning in warnings:
            logger.warning(f"Plugin '{name}': {warning}")

        incompatible = check_compatibility(manifest, CORE_VERSION)
        for reason in incompatible:
            logger.warning(f"Plugin '{name}': incompatible with this core: {reason}")
        if incompatible:
            # Surfaced through plugin status; discover_plugins sees the marker.
            manifest["_compatibility"] = incompatible
        return manifest, warnings

    @staticmethod
    def _apply_manifest(
        plugin: ManagedPlugin, manifest: dict, warnings: list[str]
    ) -> None:
        plugin.manifest = manifest
        plugin.version = str(manifest.get("version") or "")
        plugin.permission_warnings = list(warnings)
        plugin.enabled = True

    def plugin_names(self) -> list[str]:
        return list(self._plugins)

    # ------------------------------------------------------------------
    # Enable / disable / reload
    # ------------------------------------------------------------------

    def is_enabled(self, plugin_name: str) -> bool:
        """Return True when the plugin exists and is enabled."""
        plugin = self._plugins.get(plugin_name)
        return bool(plugin and plugin.enabled)

    def plugin_version(self, plugin_name: str) -> str:
        plugin = self._plugins.get(plugin_name)
        return plugin.version if plugin else ""

    def plugin_manifest(self, plugin_name: str) -> dict | None:
        plugin = self._plugins.get(plugin_name)
        return plugin.manifest if plugin else None

    def permission_warnings(self, plugin_name: str) -> list[str]:
        plugin = self._plugins.get(plugin_name)
        return list(plugin.permission_warnings) if plugin else []

    def tool_schema(self, tool_name: str) -> dict:
        """Return the tool's advertised inputSchema (empty when unknown)."""
        schema = self._tool_schemas.get(tool_name)
        return schema if isinstance(schema, dict) else {}

    def tool_risk(self, tool_name: str) -> str:
        """Return the plugin-declared risk for a tool ('' when undeclared)."""
        return self._tool_risk.get(tool_name, "")

    def report_kind(self, tool_name: str) -> str:
        """Return the report declaration ('' none, 'always', or an arg name)."""
        return self._tool_report.get(tool_name, "")

    async def disable_plugin(self, plugin_name: str) -> bool:
        """Stop a plugin and persist its disabled state."""
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            logger.error(f"Plugin '{plugin_name}' is not registered; cannot disable")
            return False

        async with plugin.lock:
            if not plugin.enabled:
                return True

            await asyncio.to_thread(self._kill_and_stop, plugin_name)
            plugin.enabled = False
            self._rebuild_tool_cache(plugin_name, [])
            self._disabled.add(plugin_name)
            self._persist_state()
            logger.info(f"Plugin '{plugin_name}' disabled")
            return True

    async def enable_plugin(self, plugin_name: str) -> bool:
        """Launch a disabled plugin again and persist the change."""
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            logger.error(f"Plugin '{plugin_name}' is not registered; cannot enable")
            return False

        async with plugin.lock:
            if plugin.enabled:
                return True

            trusted = allowed_plugins()
            if trusted and plugin_name not in trusted:
                log_security_event(
                    "plugin_rejected",
                    plugin=plugin_name,
                    reason="not_in_allowlist",
                    action="enable",
                )
                logger.error(
                    f"Plugin '{plugin_name}' is not in LOOMCORE_PLUGINS_ALLOWED; "
                    f"refusing to enable"
                )
                raise PluginPolicyDenied(plugin_name)

            self._disabled.discard(plugin_name)
            plugin.enabled = True
            plugin.restart_attempt = 0
            try:
                self._do_register_inplace(
                    plugin_name, plugin.command, plugin.working_dir
                )
                await self._initialize_plugin(plugin_name)
                tools = await self.get_tools(plugin_name)
                self._rebuild_tool_cache(plugin_name, tools)
                plugin.state = PluginState.IDLE
            except Exception as exc:
                logger.error(f"Failed to enable plugin '{plugin_name}': {exc}")
                await asyncio.to_thread(self._kill_and_stop, plugin_name)
                plugin.enabled = False
                plugin.state = PluginState.STOPPED
                self._disabled.add(plugin_name)
                self._persist_state()
                return False

            self._persist_state()
            logger.info(f"Plugin '{plugin_name}' enabled")
            return True

    async def reload_plugin(self, plugin_name: str) -> bool:
        """Restart an enabled plugin and refresh its tools."""
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            logger.error(f"Plugin '{plugin_name}' is not registered; cannot reload")
            return False

        async with plugin.lock:
            if not plugin.enabled:
                logger.warning(f"Plugin '{plugin_name}' is disabled; cannot reload")
                return False

            await asyncio.to_thread(self._kill_and_stop, plugin_name)
            plugin.restart_attempt = 0
            try:
                self._do_register_inplace(
                    plugin_name, plugin.command, plugin.working_dir
                )
                await self._initialize_plugin(plugin_name)
                tools = await self.get_tools(plugin_name)
                self._rebuild_tool_cache(plugin_name, tools)
                plugin.state = PluginState.IDLE
            except Exception as exc:
                logger.error(f"Failed to reload plugin '{plugin_name}': {exc}")
                await asyncio.to_thread(self._kill_and_stop, plugin_name)
                plugin.state = PluginState.STOPPED
                return False

            logger.info(f"Plugin '{plugin_name}' reloaded")
            return True

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        save_disabled(self._state_path, self._disabled)

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

        if not plugin.enabled:
            logger.warning(f"Plugin '{plugin_name}' is disabled; not restarting")
            return False

        async with plugin.lock:
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
                plugin.restart_delay_base * (2 ** (attempt - 1)),
                plugin.restart_delay_max,
            )

            logger.info(
                f"Restarting plugin '{plugin_name}' "
                f"({attempt}/{plugin.max_restarts}), delay {delay:.1f}s"
            )

            # Wait before restarting (non-blocking)
            await asyncio.sleep(delay)

            started = monotonic()
            try:
                # Kill the old process if it is still alive
                old_proc = plugin.proc
                if old_proc and old_proc.poll() is None:
                    await asyncio.to_thread(self._terminate_process, old_proc)

                # Launch the subprocess again in place
                self._do_register_inplace(
                    plugin_name, plugin.command, plugin.working_dir
                )

                # Wait for initialize
                await self._initialize_plugin(plugin_name)

                # Fetch tools via tools/list
                tools = await self.get_tools(plugin_name)

                # Refresh the tool cache
                self._rebuild_tool_cache(plugin_name, tools)

                plugin.state = PluginState.IDLE
                plugin.restart_attempt = 0
                logger.info(f"Plugin '{plugin_name}' restarted successfully")
                return True

            except Exception as exc:
                logger.error(f"Failed to restart plugin '{plugin_name}': {exc}")
                await asyncio.to_thread(self._kill_and_stop, plugin_name)
                plugin.state = PluginState.STOPPED
                return False
            finally:
                log_stage(
                    "plugin_restart",
                    monotonic() - started,
                    plugin=plugin_name,
                    attempt=attempt,
                    delay=delay,
                )

    def _kill_process_tree(self, proc: subprocess.Popen) -> None:
        """Kill a plugin process and every process in its group.

        The plugin runs in its own session/process group (see ``_spawn``), so the
        whole tree — including grandchildren such as ``apt-get`` or ``wg-quick``
        — is signalled together. Falls back to ``proc.kill()`` (best effort) when
        the group is already gone or cannot be signalled.
        """
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()

    def _terminate_process(self, proc: subprocess.Popen) -> None:
        """Terminate a subprocess group, escalating to kill; blocks, so run in a thread."""
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill_process_tree(proc)
            proc.wait()

    def terminate_all(self) -> None:
        """Terminate every plugin subprocess (application shutdown).

        Blocks, so callers should run it in a worker thread. Best-effort: a
        plugin that cannot be terminated is logged and skipped instead of
        aborting shutdown. Idempotent — already-dead processes are fine.
        """
        for plugin in self._plugins.values():
            proc = plugin.proc
            plugin.proc = None
            plugin.state = PluginState.STOPPED
            if proc is None or proc.poll() is not None:
                continue
            try:
                self._terminate_process(proc)
            except Exception as exc:
                logger.warning(
                    f"Failed to terminate plugin '{plugin.name}' on shutdown: {exc}"
                )

    def _do_register_inplace(self, name: str, command: list[str], working_dir: str | None):
        """Launch the subprocess again, keeping the same ManagedPlugin record."""
        proc = self._spawn(command, working_dir)
        plugin = self._plugins[name]
        plugin.proc = proc
        plugin.state = PluginState.IDLE
        self._drain_stderr(name, proc)
        logger.info(f"Plugin '{name}' subprocess restarted (PID: {proc.pid})")

    async def _initialize_plugin(self, plugin_name: str) -> None:
        """Send {"method": "initialize"} and wait for the response (10s timeout).

        Routed through the guarded write/read path so a dead process or a
        broken pipe is handled (and the process replaced) like any other call;
        a timeout kills the process, since the abandoned reader thread could
        otherwise consume a later response.
        """
        request = {"jsonrpc": "2.0", "method": "initialize"}
        try:
            await self._exchange(plugin_name, request, call_timeout=10)
        except TimeoutError:
            logger.warning(f"Plugin '{plugin_name}' initialization timed out (10s)")
            raise
        logger.info(f"Plugin '{plugin_name}' initialized")

    def _index_tools(self, plugin_name: str, tools: list[dict]) -> list[dict]:
        """Cache a plugin's tools; return the LLM-facing (non-internal) tools."""
        plugin = self._plugins.get(plugin_name)
        if plugin is not None:
            # Record every published tool (external + internal) so
            # get_plugin_status can report an accurate count.
            plugin.tools = list(tools)
        external: list[dict] = []
        for tool in tools:
            tool_name = tool.get("name")
            if not tool_name:
                continue
            owner = self._tool_plugins.get(tool_name) or self._internal_tools.get(
                tool_name
            )
            if owner is not None and owner != plugin_name:
                logger.warning(
                    f"Plugin '{plugin_name}' publishes tool '{tool_name}' already "
                    f"owned by plugin '{owner}'; ignoring the duplicate"
                )
                continue
            schema = tool.get("inputSchema")
            if isinstance(schema, dict) and schema:
                self._tool_schemas[tool_name] = schema
            risk = _normalize_risk(tool.get("risk"))
            if risk:
                self._tool_risk[tool_name] = risk
            timeout = _normalize_timeout(tool.get("timeout"), tool_name)
            if timeout is not None:
                self._tool_timeouts[tool_name] = timeout
            if tool.get("report") is True:
                self._tool_report[tool_name] = "always"
            else:
                report_arg = tool.get("report_arg")
                if isinstance(report_arg, str) and report_arg:
                    self._tool_report[tool_name] = report_arg
            if tool.get("internal"):
                self._internal_tools[tool_name] = plugin_name
            else:
                self._tool_plugins[tool_name] = plugin_name
                external.append(tool)
        return external

    def _rebuild_tool_cache(self, plugin_name: str, tools: list[dict]) -> list[dict]:
        """Rebuild the plugin's tool caches; return the LLM-facing tools."""
        # Remove this plugin's old entries from every cache.
        stale = [
            name
            for cache in (self._tool_plugins, self._internal_tools)
            for name, owner in cache.items()
            if owner == plugin_name
        ]
        for name in stale:
            self._tool_plugins.pop(name, None)
            self._internal_tools.pop(name, None)
            self._tool_schemas.pop(name, None)
            self._tool_risk.pop(name, None)
            self._tool_report.pop(name, None)
            self._tool_timeouts.pop(name, None)

        # Add new ones: internal — core only, the rest — for the LLM
        return self._index_tools(plugin_name, tools)

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
        if not plugin or not plugin.enabled or plugin.state == PluginState.STOPPED:
            return False
        return not self._mark_stopped_if_dead(plugin)

    def get_plugin_status(self, plugin_name: str) -> dict:
        """Return plugin status in the API format.

        Returns:
            {"name": ..., "state": ..., "pid": ..., "restart_attempt": ...,
             "is_healthy": ..., "enabled": ..., "version": ..., "description": ...,
             "tools": ..., "permission_warnings": ...}
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return {
                "name": plugin_name,
                "state": "stopped",
                "pid": None,
                "restart_attempt": 0,
                "is_healthy": False,
                "enabled": False,
                "version": "",
                "description": "",
                "tools": 0,
                "permission_warnings": [],
            }

        pid = plugin.proc.pid if plugin.proc is not None else None
        healthy = self.is_healthy(plugin_name)
        manifest = plugin.manifest or {}
        return {
            "name": plugin_name,
            "state": plugin.state.value,
            "pid": pid,
            "restart_attempt": plugin.restart_attempt,
            "is_healthy": healthy,
            "enabled": plugin.enabled,
            "version": plugin.version,
            "description": str(manifest.get("description") or ""),
            "tools": len(plugin.tools),
            "permission_warnings": list(plugin.permission_warnings),
        }

    # ------------------------------------------------------------------
    # Task 5: Handle BrokenPipeError / OSError in _write_request
    # ------------------------------------------------------------------

    async def _exchange(self, plugin_name: str, request: dict, call_timeout: float) -> dict:
        """Write a request and read its matching response, serialized per plugin.

        A per-plugin lock guards the whole write+read sequence. The BUSY state
        flag is only advisory: two tasks could both observe IDLE before either
        wrote, so without the lock overlapping callers could write two requests
        and then race on the single response stream, pairing a response with the
        wrong request. On timeout the process is killed and marked STOPPED — the
        reader thread may still be blocked in ``readline()`` and would otherwise
        consume a later response.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' is not registered")

        async with plugin.lock:
            request_id = request.get("id") or str(uuid.uuid4())
            request["id"] = request_id
            plugin.state = PluginState.BUSY
            written = False
            try:
                await self._write_request(plugin_name, request)
                written = True
                # A broken write can restart the process (which resets the
                # state); re-assert BUSY before reading.
                plugin.state = PluginState.BUSY
                return await asyncio.wait_for(
                    asyncio.to_thread(self._read_response, plugin_name, request_id),
                    timeout=call_timeout,
                )
            except TimeoutError:
                logger.warning(
                    f"{call_timeout:.0f}s timeout for plugin '{plugin_name}' — killing process"
                )
                await asyncio.to_thread(self._kill_and_stop, plugin_name)
                raise
            except asyncio.CancelledError:
                # Only a request that actually reached the stream can desync it.
                # A caller cancelled while still queued on the lock (written is
                # False) never touched the stream, so it must not kill the
                # process the current lock holder is reading from.
                if written:
                    logger.warning(
                        f"Exchange with plugin '{plugin_name}' cancelled mid-call "
                        "— killing process"
                    )
                    await asyncio.to_thread(self._kill_and_stop, plugin_name)
                raise
            finally:
                if plugin.state == PluginState.BUSY:
                    plugin.state = PluginState.IDLE

    async def _write_request(self, plugin_name: str, request: dict) -> str:
        """Write a JSON-RPC request to the plugin's stdin. Return the request_id.

        Callers must hold the plugin's lock (see ``_exchange``); the BUSY state
        is not checked here because the lock now provides mutual exclusion.

        If the process is dead or the input stream is broken (BrokenPipeError/OSError):
        1. Mark STOPPED and call _restart_plugin().
        2. After a successful restart, retry the write once.
        3. If the restart failed, raise RuntimeError.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' is not registered")

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

    def _read_response(self, plugin_name: str, request_id: str) -> dict:
        """Read the response matching ``request_id``, skipping notifications.

        Notifications (no ``id``) and responses for a different request are
        discarded: a stale response can only be a late reply to an earlier
        request that already timed out (whose process was killed), so returning
        it would pair the caller with the wrong result.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' is not registered")

        # Pin the process for the whole read loop. After a timeout the caller
        # kills this process and may restart the plugin; a reader that already
        # skipped a non-matching line would otherwise re-bind to the successor's
        # stdout, steal its responses (never matching its stale id) and cause a
        # kill loop.
        proc = plugin.proc
        if proc is None or proc.stdout is None:
            raise RuntimeError(f"Plugin '{plugin_name}' has no stdout stream")

        while True:
            line = proc.stdout.readline()
            if not line:
                logger.error(f"Plugin '{plugin_name}' terminated")
                plugin.state = PluginState.STOPPED
                raise RuntimeError(f"Plugin '{plugin_name}' terminated")

            try:
                response = json.loads(line.strip())
            except json.JSONDecodeError:
                # A plugin may print non-JSON log lines to stdout; skip them
                # instead of letting the reader thread die on a parse error.
                logger.debug(
                    "Plugin '%s' emitted a non-JSON stdout line; skipping",
                    plugin_name,
                )
                continue
            if not isinstance(response, dict):
                continue
            if "id" not in response:
                continue
            if response["id"] != request_id:
                logger.warning(
                    f"Plugin '{plugin_name}' returned a response for an "
                    f"unexpected request id; discarding"
                )
                continue
            return response

    async def get_tools(self, plugin_name: str) -> list[dict]:
        """Fetch a plugin's tools (tools/list → JSON Schema).

        On a dead process or STOPPED state, attempts auto-restart.
        """
        plugin = self._plugins.get(plugin_name)

        if plugin is not None and not plugin.enabled:
            raise RuntimeError(f"Plugin '{plugin_name}' is disabled")

        if not plugin or self._mark_stopped_if_dead(plugin) or plugin.state == PluginState.STOPPED:
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' before get_tools")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Plugin '{plugin_name}' is unavailable")
            plugin = self._plugins[plugin_name]

        # Check again — in case the restart failed
        if self._mark_stopped_if_dead(plugin) or plugin.state == PluginState.STOPPED:
            raise RuntimeError(f"Plugin '{plugin_name}' is unavailable (dead process)")

        started = monotonic()
        try:
            request = {"jsonrpc": "2.0", "method": "tools/list"}
            response = await self._exchange(plugin_name, request, call_timeout=30)
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Plugin '{plugin_name}' returned no result")
            return result.get("tools", [])
        finally:
            log_stage("plugin_get_tools", monotonic() - started, plugin=plugin_name)

    async def get_all_tools(self) -> list[dict]:
        """Fetch tools from all registered plugins.

        On failure to fetch a plugin's tools, attempts auto-restart.
        Returns the tools that were fetched successfully.
        """
        started = monotonic()
        all_tools: list[dict] = []
        failed_plugins: list[str] = []
        for plugin_name in self.plugin_names():
            if not self.is_enabled(plugin_name):
                continue
            try:
                tools = await self.get_tools(plugin_name)
                all_tools.extend(self._rebuild_tool_cache(plugin_name, tools))
            except Exception as exc:
                logger.warning(f"Failed to fetch tools for plugin '{plugin_name}': {exc}")
                failed_plugins.append(plugin_name)

        # Try to restart the plugins that did not respond
        for plugin_name in failed_plugins:
            plugin = self._plugins.get(plugin_name)
            if plugin is not None and plugin.state == PluginState.BUSY:
                logger.info(
                    f"Plugin '{plugin_name}' is busy; skipping auto-restart from "
                    f"get_all_tools"
                )
                continue
            logger.info(f"Attempting auto-restart of plugin '{plugin_name}' from get_all_tools")
            try:
                if await self._restart_plugin(plugin_name):
                    # _restart_plugin already refreshed the tool cache; re-read the
                    # tools only to include them in the returned list.
                    tools = await self.get_tools(plugin_name)
                    all_tools.extend(self._rebuild_tool_cache(plugin_name, tools))
            except Exception as inner_exc:
                logger.error(f"Failed to restart plugin '{plugin_name}' from get_all_tools: {inner_exc}")

        log_stage(
            "get_all_tools",
            monotonic() - started,
            plugins=len(self.plugin_names()),
            tools=len(all_tools),
            failed=len(failed_plugins),
        )
        self._tools_indexed = True
        return all_tools

    def tools_indexed(self) -> bool:
        """True once the external tool catalog has been indexed at least once."""
        return self._tools_indexed

    def get_cached_tools(self) -> list[dict]:
        """Return indexed external tool schemas without a JSON-RPC round trip.

        Internal tools and disabled plugins are excluded. Returns [] when the
        catalog has not been indexed yet.
        """
        if not self._tools_indexed:
            return []
        result: list[dict] = []
        for name in self.plugin_names():
            plugin = self._plugins.get(name)
            if plugin is None or not plugin.enabled or not self.is_healthy(name):
                continue
            for tool in plugin.tools:
                if not isinstance(tool, dict):
                    continue
                tool_name = tool.get("name")
                if not tool_name:
                    continue
                if self._tool_plugins.get(tool_name) != name:
                    continue
                result.append(tool)
        return result

    async def get_tools_for_routing(self) -> list[dict]:
        """Tool schemas for the LLM catalog: cached when indexed, else live."""
        if self._tools_indexed:
            return self.get_cached_tools()
        return await self.get_all_tools()

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
        # Internal tools are core-controlled: a bad schema warns, never blocks.
        schema = self._tool_schemas.get(tool_name)
        if schema:
            try:
                validate_tool_args(tool_name, arguments, schema)
            except ValueError as exc:
                logger.warning(f"Internal tool '{tool_name}' contract mismatch: {exc}")
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

        Timeout: the tool's declared ``timeout`` if any, else 30 seconds. If the
        plugin does not respond, kill its whole process group. On STOPPED or a
        dead process, attempts auto-restart.
        """
        plugin = self._plugins.get(plugin_name)

        if plugin is not None and not plugin.enabled:
            raise RuntimeError(f"Plugin '{plugin_name}' is disabled")

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

        call_timeout = self._tool_timeouts.get(tool_name, DEFAULT_TOOL_TIMEOUT)
        started = monotonic()
        try:
            response = await self._exchange(
                plugin_name, request, call_timeout=call_timeout
            )
            if response.get("error"):
                raise RuntimeError(response["error"].get("message", "MCP plugin error"))
            result = response.get("result")
            if result is None:
                raise RuntimeError(f"Plugin '{plugin_name}' returned no result")
            return result
        except asyncio.CancelledError:
            # _exchange already kills the process when the request had reached
            # the stream. A cancellation while merely queued on the lock must
            # not abort the unrelated in-flight call holding the lock.
            logger.warning(
                f"Call to '{tool_name}' in '{plugin_name}' cancelled"
            )
            raise
        except TimeoutError:
            raise TimeoutError(
                f"Call to '{tool_name}' in '{plugin_name}' timed out "
                f"({call_timeout:.0f}s)"
            ) from None
        finally:
            log_stage(
                "plugin_execute_tool", monotonic() - started, tool=tool_name
            )

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
                self._kill_process_tree(proc)
                proc.wait(timeout=5)
            except Exception:
                logger.exception(f"Failed to kill plugin '{plugin_name}'")
        plugin.state = PluginState.STOPPED

    async def execute_tool_by_name(
        self,
        tool_name: str,
        arguments: dict | None = None,
        *,
        approved: bool = False,
    ) -> dict:
        """Invoke a tool by name, enforcing its argument contract and approval gate.

        Argument validation always runs first. A non-safe tool (as classified by
        the core, not only as declared by the plugin) is refused unless the caller
        explicitly passes ``approved=True``; this mirrors the plan executor's
        safety invariant at the dispatch boundary.
        """
        validate_tool_args(tool_name, arguments, self._tool_schemas.get(tool_name))
        risk = classify_tool_risk(tool_name, self.tool_risk(tool_name))
        if risk != "safe" and not approved:
            raise PermissionError(f"Tool '{tool_name}' requires approval")
        result = await self.execute_tool(
            self.plugin_for_tool(tool_name), tool_name, arguments
        )
        for warning in validate_tool_result(result):
            logger.warning(f"Tool '{tool_name}' returned an unexpected result: {warning}")
        return result
