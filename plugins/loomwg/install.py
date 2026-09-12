"""Install helpers for WireGuard prerequisites.

Package installation is done on the host via deploy/install.sh before
starting the container.

setup_server() runs entirely inside the container using cap_add NET_ADMIN
for firewall/NAT and sysctl configuration.

Set LOOMWG_SKIP_FIREWALL=1 on firewalld hosts (RHEL/Rocky) to skip automatic
firewall/NAT setup and configure it manually instead.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from ipaddress import ip_network

from keys import generate_keypair
from wireguard import run_command

from config import config_path


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
