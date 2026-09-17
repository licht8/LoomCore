"""Configure the WireGuard server inside the plugin container.

setup_server() runs entirely inside the container using cap_add NET_ADMIN for
firewall/NAT and sysctl configuration:
- Config is written to /etc/wireguard (mounted rw)
- Firewall/NAT is configured via iptables/nft
- Forwarding is enabled via sysctl

Set LOOMWG_SKIP_FIREWALL=1 on firewalld hosts (RHEL/Rocky) to skip automatic
firewall/NAT setup and configure it manually instead.
"""

from __future__ import annotations

import os
import re
import shutil
from ipaddress import ip_network

from ..peers.config import config_path
from ..util.files import atomic_write_text
from ..wg.device import run_command
from ..wg.keys import generate_keypair
from .shell import run_shell

_SUPPORTED_FIREWALLS = ("firewalld", "ufw", "nftables", "iptables")


def detect_host() -> dict[str, str]:
    """Detect the OS, external interface, and firewall inside the container."""
    return {
        "os": run_shell(
            "test -r /etc/os-release && . /etc/os-release && "
            "printf '%s %s' \"${ID:-unknown}\" \"${VERSION_ID:-unknown}\""
        ),
        "external_interface": run_shell(
            "ip -4 route show default | awk 'NR==1 {print $5}'"
        ),
        "firewall": run_shell(
            "if command -v firewall-cmd >/dev/null; then printf firewalld; "
            "elif command -v ufw >/dev/null; then printf ufw; "
            "elif command -v nft >/dev/null; then printf nftables; "
            "elif command -v iptables >/dev/null; then printf iptables; "
            "else printf none; fi"
        ),
    }


def resolve_firewall(host_firewall: str) -> str:
    """Resolve the firewall to use: a supported backend or 'skipped'."""
    if os.environ.get("LOOMWG_SKIP_FIREWALL") == "1":
        # firewalld hosts (RHEL/Rocky): the container cannot talk to host
        # firewalld, so the operator opens the port and NAT manually
        # (see VDS_RUNBOOK.md).
        return "skipped"
    if host_firewall not in _SUPPORTED_FIREWALLS:
        raise RuntimeError(
            "No supported firewall found. Open the UDP port and configure NAT manually."
        )
    return host_firewall


def firewall_hooks(
    firewall: str, external_interface: str, ipv4_network: str
) -> tuple[str, str]:
    """Build the wg-quick PostUp/PostDown hooks for the resolved firewall.

    The hooks install the forward-accept rules and a network-scoped MASQUERADE
    with iptables; they are emitted whenever iptables is usable, including ufw
    hosts (ufw has no NAT command of its own). They are skipped when the firewall
    was skipped (the operator configures the host firewall manually) and on hosts
    without iptables (nft-only), where the command would make wg-quick fail and
    tear the interface down again; configure_firewall() installs NAT there.
    """
    if firewall == "skipped" or shutil.which("iptables") is None:
        return "", ""
    up = (
        "iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; "
        f"iptables -t nat -A POSTROUTING -s {ipv4_network} -o {external_interface} "
        "-j MASQUERADE; ip6tables -A FORWARD -i %i -j ACCEPT; "
        "ip6tables -A FORWARD -o %i -j ACCEPT"
    )
    down = (
        "iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; "
        f"iptables -t nat -D POSTROUTING -s {ipv4_network} -o {external_interface} "
        "-j MASQUERADE; ip6tables -D FORWARD -i %i -j ACCEPT; "
        "ip6tables -D FORWARD -o %i -j ACCEPT"
    )
    return up, down


def configure_firewall(
    firewall: str,
    interface: str,
    external_interface: str,
    listen_port: int,
    ipv4_network: str,
) -> str:
    """Configure the firewall inside the container (no nsenter/chroot)."""
    if firewall == "firewalld":
        run_shell(
            f"firewall-cmd --permanent --add-port={listen_port}/udp && "
            "firewall-cmd --permanent --add-masquerade && firewall-cmd --reload"
        )
    elif firewall == "ufw":
        run_shell(f"ufw allow {listen_port}/udp")
        run_shell("ufw route allow in on " + interface + " out on " + external_interface)
    elif firewall == "nftables":
        run_shell(
            "nft add table inet loomwg 2>/dev/null || true; "
            "nft 'add chain inet loomwg input { type filter hook input priority 0; policy accept; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg input udp dport {listen_port} accept 2>/dev/null || true; "
            "nft 'add chain inet loomwg postrouting { type nat hook postrouting priority 100; }' 2>/dev/null || true; "
            f"nft add rule inet loomwg postrouting oifname \"{external_interface}\" masquerade 2>/dev/null || true"
        )
    elif firewall == "iptables":
        # NAT is owned by the wg-quick PostUp/PostDown hooks (firewall_hooks),
        # which are emitted on every iptables-capable host. Installing the rule
        # here as well would add a second identical entry that PostDown's `-D`
        # (deleting only the first match) leaves behind after teardown.
        run_shell(
            f"iptables -C INPUT -p udp --dport {listen_port} -j ACCEPT 2>/dev/null || "
            f"iptables -I INPUT -p udp --dport {listen_port} -j ACCEPT"
        )
    else:
        raise RuntimeError(
            "No supported firewall found. Open the UDP port and configure NAT manually."
        )
    return firewall


def enable_forwarding() -> None:
    """Enable forwarding via sysctl directly (no filesystem access)."""
    run_shell("sysctl -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1")


def setup_server(arguments: dict) -> dict:
    """Configure the WireGuard server inside the container."""
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
    host = detect_host()
    external_interface = str(arguments.get("external_interface") or host["external_interface"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", external_interface):
        raise ValueError("Could not determine the external network interface")
    firewall = resolve_firewall(host["firewall"])
    post_up, post_down = firewall_hooks(firewall, external_interface, str(v4))
    lines = [
        "[Interface]",
        f"Address = {address_v4}, {address_v6}",
        f"PrivateKey = {keypair['private_key']}",
        f"ListenPort = {listen_port}",
        f"DNS = {', '.join(str(item).strip() for item in dns)}",
    ]
    if post_up:
        lines.append(f"PostUp = {post_up}")
        lines.append(f"PostDown = {post_down}")
    lines.append("")
    atomic_write_text(path, "\n".join(lines), mode=0o600)

    enable_forwarding()
    if firewall != "skipped":
        configure_firewall(firewall, interface, external_interface, listen_port, str(v4))

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
