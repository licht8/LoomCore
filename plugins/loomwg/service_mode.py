"""App-managed WireGuard autostart.

The plugin runs inside a container (host network), so it cannot drive host
systemd. Instead it persists an autostart flag next to the configs and brings
the marked interfaces up when the plugin starts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from results import text_result
from wireguard import (
    CommandError,
    active_interfaces,
    config_directory,
    configured_interfaces,
    run_command,
    validate_interface,
)

logger = logging.getLogger("loomwg.service")


def _state_path() -> Path:
    return config_directory() / ".loomwg" / "autostart.json"


def _read_state() -> dict[str, bool]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(name): bool(enabled) for name, enabled in data.items()}


def _write_state(state: dict[str, bool]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def enable(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    state = _read_state()
    state[interface] = True
    _write_state(state)

    started = False
    if interface not in set(active_interfaces()):
        run_command(["wg-quick", "up", interface], timeout=60)
        started = True
    return text_result(
        f"Autostart enabled for {interface}" + (" (started now)" if started else ""),
        interface=interface,
        autostart=True,
        started=started,
    )


def disable(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)
    state = _read_state()
    state.pop(interface, None)
    _write_state(state)
    return text_result(
        f"Autostart disabled for {interface} "
        "(interface left running; use wg_stop to stop it)",
        interface=interface,
        autostart=False,
    )


def apply_autostart() -> None:
    """Bring up all interfaces marked for autostart (called at plugin start)."""
    state = _read_state()
    if not state:
        return
    configured = set(configured_interfaces())
    active = set(active_interfaces())
    for interface, enabled in state.items():
        if not enabled or interface in active or interface not in configured:
            continue
        try:
            run_command(["wg-quick", "up", interface], timeout=60)
            logger.info("autostart: brought up %s", interface)
        except CommandError as exc:
            logger.warning("autostart failed for %s: %s", interface, exc)
