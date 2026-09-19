"""Install WireGuard prerequisites inside the plugin container.

install_wireguard() installs missing userspace packages with the container's own
package manager and loads the WireGuard kernel module via SYS_MODULE + the
read-only /lib/modules mount. The plugin cannot install host packages or kernel
modules, so a kernel without WireGuard support returns host instructions instead.
"""

from __future__ import annotations

import contextlib
import shutil
from typing import Any

from ..results import text_result
from ..wg.device import CommandError, run_command
from .shell import run_shell

# Tools the plugin needs. `iptables`/`nft` are alternatives: at least one
# firewall backend must be present.
_REQUIRED_TOOLS = ("wg", "wg-quick", "ip", "qrencode", "modprobe")
_FIREWALL_TOOLS = ("iptables", "nft")

# Package name per tool, per package manager. Only the container's own
# filesystem is touched; the host is never modified.
_PACKAGES: dict[str, dict[str, str]] = {
    "apt": {
        "wg": "wireguard-tools",
        "wg-quick": "wireguard-tools",
        "ip": "iproute2",
        "qrencode": "qrencode",
        "modprobe": "kmod",
        "iptables": "iptables",
        "nft": "nftables",
    },
    "dnf": {
        "wg": "wireguard-tools",
        "wg-quick": "wireguard-tools",
        "ip": "iproute",
        "qrencode": "qrencode",
        "modprobe": "kmod",
        "iptables": "iptables-nft",
        "nft": "nftables",
    },
    "yum": {
        "wg": "wireguard-tools",
        "wg-quick": "wireguard-tools",
        "ip": "iproute",
        "qrencode": "qrencode",
        "modprobe": "kmod",
        "iptables": "iptables",
        "nft": "nftables",
    },
    "apk": {
        "wg": "wireguard-tools",
        "wg-quick": "wireguard-tools",
        "ip": "iproute2",
        "qrencode": "qrencode",
        "modprobe": "kmod",
        "iptables": "iptables",
        "nft": "nftables",
    },
}

_INSTALL_COMMANDS = {
    "apt": "apt-get update && apt-get install -y --no-install-recommends {packages}",
    "dnf": "dnf install -y {packages}",
    "yum": "yum install -y {packages}",
    "apk": "apk add --no-cache {packages}",
}

# Transient interface used to verify kernel WireGuard support (built-in or
# module). Created and torn down on every path by _kernel_support_available().
_PROBE_INTERFACE = "loomwgprobe"

_KERNEL_HINT = (
    "The host kernel does not provide WireGuard. The plugin cannot install "
    "kernel modules on the host. On the HOST install the module for your "
    "kernel (Debian/Ubuntu: apt-get install -y wireguard-dkms; RHEL/Rocky: "
    "dnf install -y kmod-wireguard) or upgrade to a kernel that includes "
    "WireGuard (Linux 5.6+), then retry."
)


def detect_package_manager() -> str:
    """Return the package manager available in the container (or '')."""
    for binary, manager in (
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("yum", "yum"),
        ("apk", "apk"),
    ):
        if shutil.which(binary):
            return manager
    return ""


def missing_tools() -> list[str]:
    """List required tools that are not on PATH in the container."""
    missing = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
    if not any(shutil.which(tool) for tool in _FIREWALL_TOOLS):
        missing.extend(_FIREWALL_TOOLS)
    return missing


def dependency_report() -> dict[str, Any]:
    """Read-only snapshot of the plugin's WireGuard prerequisites."""
    missing = missing_tools()
    return {
        "tools": {
            tool: shutil.which(tool) is not None
            for tool in (*_REQUIRED_TOOLS, *_FIREWALL_TOOLS)
        },
        "missing": missing,
        "package_manager": detect_package_manager(),
        "ready": not missing,
    }


def _packages_for(manager: str, tools: list[str]) -> list[str]:
    mapping = _PACKAGES.get(manager, {})
    packages: list[str] = []
    for tool in tools:
        package = mapping.get(tool)
        if package and package not in packages:
            packages.append(package)
    return packages


def ensure_dependencies() -> dict[str, Any]:
    """Install any missing WireGuard packages inside the container."""
    missing = missing_tools()
    manager = detect_package_manager()
    if not missing:
        return {
            "changed": False,
            "package_manager": manager,
            "missing_before": [],
            "missing_after": [],
            "message": "All WireGuard prerequisites are already installed.",
        }
    if not manager:
        raise RuntimeError(
            "Missing WireGuard prerequisites ("
            + ", ".join(missing)
            + ") and no supported package manager (apt-get/dnf/yum/apk) was found."
        )
    packages = _packages_for(manager, missing)
    if not packages:
        raise RuntimeError(
            "No package mapping for " + ", ".join(missing) + " on " + manager
        )
    command = _INSTALL_COMMANDS[manager].format(packages=" ".join(packages))
    run_shell(command)
    still_missing = missing_tools()
    if still_missing:
        raise RuntimeError("Installation did not provide: " + ", ".join(still_missing))
    return {
        "changed": True,
        "package_manager": manager,
        "packages": packages,
        "missing_before": missing,
        "missing_after": [],
        "message": f"Installed {', '.join(packages)} via {manager}.",
    }


def _remove_probe_link() -> None:
    """Best-effort teardown of the throwaway probe interface."""
    with contextlib.suppress(CommandError):
        run_command(["ip", "link", "delete", "dev", _PROBE_INTERFACE], timeout=30)


def _kernel_support_available() -> bool:
    """Probe the host kernel for WireGuard support with a throwaway link."""
    # An earlier probe may have died before tearing its link down: a leftover
    # link makes the add below fail with "File exists", which would report a
    # supported kernel as unsupported.
    _remove_probe_link()
    try:
        run_command(
            ["ip", "link", "add", "dev", _PROBE_INTERFACE, "type", "wireguard"],
            timeout=30,
        )
    except CommandError:
        return False
    finally:
        _remove_probe_link()
    return True


def ensure_kernel_module() -> dict[str, Any]:
    """Load the WireGuard kernel module and verify support (best effort)."""
    with contextlib.suppress(CommandError):
        run_command(["modprobe", "wireguard"], timeout=30)
    if _kernel_support_available():
        return {
            "module": "available",
            "loaded": True,
            "message": "WireGuard kernel support is available.",
        }
    return {"module": "missing", "loaded": False, "message": _KERNEL_HINT}


def install_wireguard(arguments: dict) -> dict[str, Any]:
    """Install missing prerequisites and load the kernel module."""
    dependencies = ensure_dependencies()
    module = ensure_kernel_module()
    message = dependencies["message"] + "\n" + module["message"]
    if not module["loaded"]:
        message += "\nWireGuard is not usable until the kernel module is available."
    return text_result(
        message,
        ready=module["loaded"],
        changed=dependencies["changed"],
        dependencies=dependencies,
        kernel_module=module,
    )
