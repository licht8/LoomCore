"""WireGuard interface lifecycle operations."""

from __future__ import annotations

from wireguard import active_interfaces, run_command, validate_interface


def start(arguments: dict) -> dict:
    interface = validate_interface(arguments)
    if interface in set(active_interfaces()):
        return {"interface": interface, "status": "already_running"}
    run_command(["wg-quick", "up", interface], timeout=60)
    return {"interface": interface, "status": "started"}


def stop(arguments: dict) -> dict:
    interface = validate_interface(arguments)
    if interface not in set(active_interfaces()):
        return {"interface": interface, "status": "already_stopped"}
    run_command(["wg-quick", "down", interface], timeout=60)
    return {"interface": interface, "status": "stopped"}


def restart(arguments: dict) -> dict:
    interface = validate_interface(arguments)
    if interface in set(active_interfaces()):
        run_command(["wg-quick", "down", interface], timeout=60)
    run_command(["wg-quick", "up", interface], timeout=60)
    return {"interface": interface, "status": "restarted"}


def format_lifecycle(result: dict) -> str:
    labels = {
        "started": "запущен",
        "stopped": "остановлен",
        "restarted": "перезапущен",
        "already_running": "уже запущен",
        "already_stopped": "уже остановлен",
    }
    return f"{result['interface']}: {labels.get(result['status'], result['status'])}"
