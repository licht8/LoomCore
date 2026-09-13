"""Install helpers for WireGuard prerequisites.

install_wireguard() runs inside the container: it installs missing userspace
packages with the container's own package manager and loads the WireGuard
kernel module via SYS_MODULE + the read-only /lib/modules mount. The plugin
cannot install host packages or kernel modules, so a kernel without WireGuard
support returns host instructions instead.

setup_server() runs entirely inside the container using cap_add NET_ADMIN
for firewall/NAT and sysctl configuration.

Set LOOMWG_SKIP_FIREWALL=1 on firewalld hosts (RHEL/Rocky) to skip automatic
firewall/NAT setup and configure it manually instead.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from ipaddress import ip_network
from typing import Any

from ..peers.config import config_path
from ..results import text_result
from ..wg.device import CommandError, run_command
from ..wg.keys import generate_keypair

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
# module). Created and immediately removed by ensure_kernel_module().
_PROBE_INTERFACE = "loomwgprobe"

_KERNEL_HINT = (
    "The host kernel does not provide WireGuard. The plugin cannot install "
    "kernel modules on the host. On the HOST install the module for your "
    "kernel (Debian/Ubuntu: apt-get install -y kmod-wireguard; RHEL/Rocky: "
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
    _container_shell(command)
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


def _kernel_support_available() -> bool:
    """Probe the host kernel for WireGuard support with a throwaway link."""
    try:
        run_command(
            ["ip", "link", "add", "dev", _PROBE_INTERFACE, "type", "wireguard"],
            timeout=30,
        )
    except CommandError:
        return False
    with contextlib.suppress(CommandError):
        run_command(["ip", "link", "delete", "dev", _PROBE_INTERFACE], timeout=30)
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


def _container_shell(command: str, timeout: int = 300) -> str:
    """Run a command inside the container (no nsenter/chroot).

    The container has the NET_ADMIN capability, so iptables/nft/sysctl
    commands run directly.
    """
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "no details"
        raise RuntimeError(
            f"Command exited with code {result.returncode}: {details}"
        )
    return result.stdout.strip()


def _detect_host() -> dict[str, str]:
    """Detect the OS, external interface, and firewall inside the container."""
    return {
        "os": _container_shell(
            "test -r /etc/os-release && . /etc/os-release && "
            "printf '%s %s' \"${ID:-unknown}\" \"${VERSION_ID:-unknown}\""
        ),
        "external_interface": _container_shell(
            "ip -4 route show default | awk 'NR==1 {print $5}'"
        ),
        "firewall": _container_shell(
            "if command -v firewall-cmd >/dev/null; then printf firewalld; "
            "elif command -v ufw >/dev/null; then printf ufw; "
            "elif command -v nft >/dev/null; then printf nftables; "
            "elif command -v iptables >/dev/null; then printf iptables; "
            "else printf none; fi"
        ),
    }


def _configure_firewall(
    firewall: str,
    interface: str,
    external_interface: str,
    listen_port: int,
    ipv4_network: str,
) -> str:
    """Configure the firewall inside the container (no nsenter/chroot)."""
    if firewall == "firewalld":
        _container_shell(
            f"firewall-cmd --permanent --add-port={listen_port}/udp && "
            "firewall-cmd --permanent --add-masquerade && firewall-cmd --reload"
        )
    elif firewall == "ufw":
        _container_shell(f"ufw allow {listen_port}/udp")
        _container_shell("ufw route allow in on " + interface + " out on " + external_interface)
    elif firewall == "nftables":
        _container_shell(
            "nft add table inet loomwg 2>/dev/null || true; "
            "nft 'add chain inet loomwg input { type filter hook input priority 0; policy accept; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg input udp dport {listen_port} accept 2>/dev/null || true; "
            "nft 'add chain inet loomwg postrouting { type nat hook postrouting priority 100; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg postrouting oifname \"{external_interface}\" masquerade 2>/dev/null || true"
        )
    elif firewall == "iptables":
        _container_shell(
            f"iptables -C INPUT -p udp --dport {listen_port} -j ACCEPT 2>/dev/null || "
            f"iptables -I INPUT -p udp --dport {listen_port} -j ACCEPT; "
            f"iptables -t nat -C POSTROUTING -s {ipv4_network} -o {external_interface} -j MASQUERADE 2>/dev/null || "
            f"iptables -t nat -A POSTROUTING -s {ipv4_network} -o {external_interface} -j MASQUERADE"
        )
    else:
        raise RuntimeError(
            "No supported firewall found. Open the UDP port and configure NAT manually."
        )
    return firewall


def _enable_forwarding() -> None:
    """Enable forwarding via sysctl directly (no filesystem access)."""
    _container_shell(
        "sysctl -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1"
    )


def setup_server(arguments: dict) -> dict:
    """Configure the WireGuard server inside the container.

    Runs entirely inside the container:
    - Config is written to /etc/wireguard (mounted rw)
    - Firewall/NAT is configured via iptables/nft (cap_add NET_ADMIN)
    - Forwarding is enabled via /etc/sysctl.d
    """
    interface = str(arguments.get("interface", "wg0")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{1,15}", interface):
        raise ValueError("Invalid WireGuard interface name")

    ipv4_network = str(arguments.get("ipv4_network", "10.66.66.0/24")).strip()
    ipv6_network = str(arguments.get("ipv6_network", "fd42:42:42::/64")).strip()
    try:
        v4 = ip_network(ipv4_network)
        v6 = ip_network(ipv6_network)
    except ValueError as exc:
        raise ValueError("Invalid IPv4 or IPv6 network") from exc
    if v4.version != 4 or v6.version != 6:
        raise ValueError("An IPv4 network and an IPv6 network are required")

    listen_port = int(arguments.get("listen_port", 51820))
    if not 1 <= listen_port <= 65535:
        raise ValueError("Port must be in the range 1-65535")
    dns = arguments.get("dns", ["1.1.1.1", "1.0.0.1"])
    if not isinstance(dns, list) or not dns or any(not str(item).strip() for item in dns):
        raise ValueError("dns must be a non-empty array of addresses")

    path = config_path(interface)
    if path.exists():
        raise ValueError(f"Configuration {path} already exists")

    run_command(["wg", "--version"])

    keypair = generate_keypair()
    address_v4 = f"{v4.network_address + 1}/{v4.prefixlen}"
    address_v6 = f"{v6.network_address + 1}/{v6.prefixlen}"
    host = _detect_host()
    external_interface = str(arguments.get("external_interface") or host["external_interface"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", external_interface):
        raise ValueError("Could not determine the external network interface")
    content = "\n".join(
        [
            "[Interface]",
            f"Address = {address_v4}, {address_v6}",
            f"PrivateKey = {keypair['private_key']}",
            f"ListenPort = {listen_port}",
            f"DNS = {', '.join(str(item).strip() for item in dns)}",
            "PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o "
            + external_interface
            + " -j MASQUERADE; ip6tables -A FORWARD -i %i -j ACCEPT; ip6tables -A FORWARD -o %i -j ACCEPT",
            "PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o "
            + external_interface
            + " -j MASQUERADE; ip6tables -D FORWARD -i %i -j ACCEPT; ip6tables -D FORWARD -o %i -j ACCEPT",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise

    _enable_forwarding()
    if os.environ.get("LOOMWG_SKIP_FIREWALL") == "1":
        # firewalld hosts (RHEL/Rocky): the container cannot talk to host
        # firewalld, so open the UDP port and NAT manually (see VDS_RUNBOOK.md).
        firewall = "skipped"
    else:
        firewall = _configure_firewall(
            host["firewall"],
            interface,
            external_interface,
            listen_port,
            str(v4),
        )

    message = (
        f"Server configuration {interface} created. The interface has not been started yet. "
        f"Firewall: {firewall}."
    )
    if firewall == "skipped":
        message += (
            f" Configure it manually: open UDP {listen_port} and enable masquerade "
            f"for {external_interface} (e.g. firewall-cmd --permanent "
            f"--add-port={listen_port}/udp --add-masquerade && firewall-cmd --reload)."
        )

    return {
        "interface": interface,
        "config_path": str(path),
        "server_public_key": keypair["public_key"],
        "ipv4_network": str(v4),
        "ipv6_network": str(v6),
        "listen_port": listen_port,
        "dns": dns,
        "external_interface": external_interface,
        "firewall": firewall,
        "forwarding_enabled": True,
        "message": message,
    }
