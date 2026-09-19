"""Read-only view of the WireGuard interface configuration."""

from __future__ import annotations

import re
from typing import Any

from ..results import text_result
from ..wg.config_parser import parse_sections
from ..wg.device import (
    CommandError,
    active_interfaces,
    config_directory,
    run_command,
    validate_interface,
)

_SECRET_RE = re.compile(r"^\s*(PrivateKey|PresharedKey)\s*=", re.IGNORECASE)


def _mask(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if _SECRET_RE.match(line):
            key = line.split("=", 1)[0].strip()
            lines.append(f"{key} = (masked)")
        else:
            lines.append(line)
    return "\n".join(lines)


def _summarize(text: str) -> dict[str, Any]:
    interface: dict[str, Any] = {}
    peers: list[dict[str, Any]] = []
    for section in parse_sections(text):
        name = section.name.lower()
        if name == "interface":
            for key, value in section.entries:
                if value is None:
                    continue
                lowered = key.lower()
                if lowered == "address":
                    interface["addresses"] = [
                        item.strip() for item in value.split(",") if item.strip()
                    ]
                elif lowered == "listenport":
                    interface["listen_port"] = value
                elif lowered == "fwmark":
                    interface["fwmark"] = value
        elif name == "peer":
            current: dict[str, Any] = {
                "preshared_key": False,
                "endpoint": None,
                "allowed_ips": [],
            }
            for key, value in section.entries:
                if value is None:
                    continue
                lowered = key.lower()
                if lowered == "publickey":
                    current["public_key"] = value
                elif lowered == "presharedkey":
                    current["preshared_key"] = True
                elif lowered == "allowedips":
                    current["allowed_ips"] = [
                        item.strip() for item in value.split(",") if item.strip()
                    ]
                elif lowered == "endpoint":
                    current["endpoint"] = value
            peers.append(current)
    return {"interface": interface, "peers": peers}


def show_config(arguments: dict[str, Any]) -> dict[str, Any]:
    interface = validate_interface(arguments)

    try:
        text = run_command(["wg", "showconf", interface], sensitive=True)
        source = "live"
        active = True
    except CommandError:
        # Only fall back to the on-disk config when the interface is genuinely
        # not active. If it is listed but `wg showconf` still failed (missing
        # binary, permission denied, unsupported flag, ...) re-raise instead of
        # misreporting the interface as inactive. The check itself propagates
        # infrastructure errors.
        if interface in set(active_interfaces()):
            raise
        path = config_directory() / f"{interface}.conf"
        if not path.is_file():
            raise CommandError(f"Configuration for {interface} not found") from None
        text = path.read_text(encoding="utf-8")
        source = "file"
        active = False

    display = _mask(text) or "(empty configuration)"
    if source == "live":
        header = f"{interface}: live configuration (source=live)"
    else:
        header = (
            f"{interface}: saved file (source=file; the interface is not active). "
            "This is not live state — use wg_list_interfaces or "
            "wg_interface_status for the current status."
        )
    return text_result(
        f"{header}\n\n{display}",
        interface=interface,
        source=source,
        active=active,
        config=_summarize(text),
    )
