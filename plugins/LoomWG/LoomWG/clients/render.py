"""Render WireGuard client configuration text."""

from __future__ import annotations

from ipaddress import ip_address

# A config line is "Key = value"; a value carrying a newline would smuggle in
# whole extra directives (wg-quick executes PostUp/PreUp hooks), so the text is
# guarded here, at the last point where it is composed.
_CONTROL_CHARACTERS = frozenset(chr(code) for code in range(32)) | {chr(127)}


def _reject_control_chars(value: str) -> str:
    """Refuse a config value that could inject extra directives."""
    text = str(value)
    if any(char in _CONTROL_CHARACTERS for char in text):
        raise ValueError(
            f"Config value must not contain control characters or newlines: {text!r}"
        )
    return text


def _join_values(values: list[str]) -> str:
    """Comma-join config list values, refusing control characters."""
    return ", ".join(_reject_control_chars(value) for value in values)


def format_endpoint(endpoint: str, port: int) -> str:
    """Format endpoint, bracketing IPv6 addresses per WireGuard spec."""
    endpoint = _reject_control_chars(endpoint)
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
            f"Address = {_join_values(addresses)}",
            f"PrivateKey = {_reject_control_chars(private_key)}",
            f"DNS = {_join_values(dns or ['1.1.1.1', '1.0.0.1'])}",
            "",
            "[Peer]",
            f"PublicKey = {_reject_control_chars(server_public_key)}",
            f"PresharedKey = {_reject_control_chars(preshared_key)}",
            f"Endpoint = {format_endpoint(endpoint, listen_port)}",
            "AllowedIPs = 0.0.0.0/0, ::/0",
            "PersistentKeepalive = 25",
            "",
        ]
    )
