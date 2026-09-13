"""Render WireGuard client configuration text."""

from __future__ import annotations

from ipaddress import ip_address


def format_endpoint(endpoint: str, port: int) -> str:
    """Format endpoint, bracketing IPv6 addresses per WireGuard spec."""
    try:
        ip_address(endpoint)
    except ValueError:
        pass
    else:
        if ":" in endpoint:
            endpoint = f"[{endpoint}]"
    return f"{endpoint}:{port}"


def build_client_config(
    server_public_key: str,
    listen_port: int,
    addresses: list[str],
    private_key: str,
    preshared_key: str,
    endpoint: str,
    dns: list[str] | None = None,
) -> str:
    """Render a full-tunnel client config (private key included)."""
    return "\n".join(
        [
            "[Interface]",
            f"Address = {', '.join(addresses)}",
            f"PrivateKey = {private_key}",
            f"DNS = {', '.join(dns or ['1.1.1.1', '1.0.0.1'])}",
            "",
            "[Peer]",
            f"PublicKey = {server_public_key}",
            f"PresharedKey = {preshared_key}",
            f"Endpoint = {format_endpoint(endpoint, listen_port)}",
            "AllowedIPs = 0.0.0.0/0, ::/0",
            "PersistentKeepalive = 25",
            "",
        ]
    )
