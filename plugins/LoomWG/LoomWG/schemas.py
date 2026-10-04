"""MCP tool schemas advertised by the LoomWG plugin."""

from __future__ import annotations

LIFECYCLE_TOOLS = (
    ("wg_interface_start", "Start the WireGuard interface"),
    ("wg_interface_stop", "Stop the WireGuard interface"),
    ("wg_interface_restart", "Restart the WireGuard interface"),
)


TOOLS_SCHEMA = [
    {
        "name": "wg_list_interfaces",
        "description": "List WireGuard interfaces with activity and config presence",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wg_setup_server",
        "description": (
            "Create the WireGuard server configuration after admin approval. "
            "Defaults are safe: wg0, 10.66.66.0/24, fd42:42:42::/64, port 51820, "
            "DNS 1.1.1.1 and 1.0.0.1. For full-tunnel it also enables forwarding, "
            "NAT, and a firewall rule."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "ipv4_network": {"type": "string", "default": "10.66.66.0/24"},
                "ipv6_network": {"type": "string", "default": "fd42:42:42::/64"},
                "listen_port": {"type": "integer", "default": 51820},
                "dns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["1.1.1.1", "1.0.0.1"],
                },
                "external_interface": {
                    "type": "string",
                    "description": "Optional external interface; otherwise detected via default route",
                },
            },
            "required": [],
        },
    },
    {
        "name": "wg_interface_status",
        "description": "Show the WireGuard interface status and peer activity",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_list_peers",
        "description": "List WireGuard peers with handshake and traffic",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_diagnostics",
        "description": "Check WireGuard installation, interface, link, and configuration",
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    },
    {
        "name": "wg_install_dependencies",
        "description": (
            "Install missing WireGuard userspace tools inside the plugin's "
            "container and load the WireGuard kernel module. If the host kernel "
            "lacks WireGuard support it returns the host-side install commands. "
            "Requires admin approval."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wg_create_peer",
        "description": (
            "Create/track a client. Without public_key a keypair is generated and "
            "the client is saved to the store; with public_key an existing peer is "
            "tracked. Idempotent for the same key and allowed IPs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "interface": {"type": "string", "default": "wg0"},
                "public_key": {"type": "string"},
                "addresses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional client addresses; auto-allocated when omitted",
                },
                "allowed_ips": {"type": "array", "items": {"type": "string"}},
                "preshared_key": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "wg_remove_peer",
        "description": "Remove a client/peer by name (JSON store) or by public key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "interface": {"type": "string", "default": "wg0"},
                "name": {"type": "string"},
                "public_key": {"type": "string"},
            },
            "required": [],
        },
    },
]

TOOLS_SCHEMA.extend(
    {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"interface": {"type": "string", "default": "wg0"}},
            "required": [],
        },
    }
    for name, description in LIFECYCLE_TOOLS
)

TOOLS_SCHEMA.append(
    {
        "name": "wg_get_client_artifact",
        "description": "Internal access to a stored client's config or QR code",
        "internal": True,
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["config", "qr"]},
            },
            "required": ["name", "kind"],
        },
    }
)

TOOLS_SCHEMA.extend(
    [
        {
            "name": "wg_show_interface_config",
            "description": (
                "Show the interface configuration (live, or the saved file if the "
                "interface is down). Private and preshared keys are always masked."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                },
                "required": [],
            },
        },
        {
            "name": "wg_check_interface_config",
            "description": (
                "Validate a WireGuard config without starting it: sections, keys, "
                "CIDRs, duplicate peers, and subnet overlaps."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional: validate this config file instead of the "
                            "interface file. Must resolve inside the WireGuard "
                            "config directory."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional: validate this config text instead of a file",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "wg_stats",
            "description": (
                "Snapshot statistics: total traffic, per-peer share, and the top "
                "peers by traffic (no history)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "top": {"type": "integer", "default": 5, "minimum": 1},
                },
                "required": [],
            },
        },
        {
            "name": "wg_export_existing_clients",
            "description": (
                "Import untracked peers and (re)issue config + QR for the clients "
                "on an interface. Backfilled peers have no stored private key, so "
                "rotate (default true) recreates them with new keys. Re-running is "
                "safe: clients with stored keys are only reissued. Requires admin "
                "approval."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "rotate": {"type": "boolean", "default": True},
                    "kind": {
                        "type": "string",
                        "enum": ["config", "qr", "both"],
                        "default": "both",
                    },
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of client names to process",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "wg_service_enable",
            "description": (
                "Enable autostart: bring the interface up now and on plugin start "
                "(app-managed; no host systemd)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        },
        {
            "name": "wg_service_disable",
            "description": (
                "Disable autostart for the interface (does not stop it; use wg_interface_stop)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"interface": {"type": "string", "default": "wg0"}},
                "required": [],
            },
        },
    ]
)

def _declare_risk(tools: list[dict], risk: dict[str, str]) -> None:
    """Attach the declared risk to every tool, refusing to guess.

    An advertised tool without a risk entry (a newly added tool or a typo)
    must never be silently treated as safe, and a risk entry without a tool
    means the declaration went stale.
    """
    names = {tool["name"] for tool in tools}
    missing = sorted(names - set(risk))
    unknown = sorted(set(risk) - names)
    if missing or unknown:
        raise RuntimeError(
            "Risk declarations do not match the advertised tools: "
            f"missing={missing}, unknown={unknown}"
        )
    for tool in tools:
        tool["risk"] = risk[tool["name"]]


def _declare_timeouts(tools: list[dict], timeouts: dict[str, float]) -> None:
    """Declare per-tool call timeouts; refuse stale names.

    Timeouts are opt-in (like reports): a tool without an entry uses the core
    default (30s). A declared name without a matching tool means the declaration
    went stale and is an error.
    """
    names = {tool["name"] for tool in tools}
    unknown = sorted(set(timeouts) - names)
    if unknown:
        raise RuntimeError(
            f"Timeout declarations reference unknown tools: {unknown}"
        )
    for tool in tools:
        if tool["name"] in timeouts:
            tool["timeout"] = timeouts[tool["name"]]


def _declare_report(tools: list[dict], report: set[str]) -> None:
    """Mark the listed tools as reports; refuse stale names.

    Report-ness is opt-in (unlike ``_declare_risk``), so tools not listed stay
    untouched. A declared name without a matching tool means the declaration
    went stale and is an error.
    """
    names = {tool["name"] for tool in tools}
    unknown = sorted(report - names)
    if unknown:
        raise RuntimeError(
            f"Report declarations reference unknown tools: {unknown}"
        )
    for tool in tools:
        if tool["name"] in report:
            tool["report"] = True


# Per-tool risk declared to the core (Phase 1b). Read-only tools are safe,
# tracking a new client is moderate, and anything that changes the interface,
# keys or autostart is dangerous.
_TOOL_RISK = {
    "wg_list_interfaces": "safe",
    "wg_interface_status": "safe",
    "wg_list_peers": "safe",
    "wg_diagnostics": "safe",
    "wg_show_interface_config": "safe",
    "wg_check_interface_config": "safe",
    "wg_stats": "safe",
    "wg_get_client_artifact": "safe",
    "wg_create_peer": "moderate",
    "wg_remove_peer": "dangerous",
    "wg_setup_server": "dangerous",
    "wg_install_dependencies": "dangerous",
    "wg_interface_start": "dangerous",
    "wg_interface_stop": "dangerous",
    "wg_interface_restart": "dangerous",
    "wg_service_enable": "dangerous",
    "wg_service_disable": "dangerous",
    "wg_export_existing_clients": "dangerous",
}

_declare_risk(TOOLS_SCHEMA, _TOOL_RISK)

# Listings/reports whose raw output already is the answer: the turn skips the
# summary LLM call. These are all safe read-only tools.
_REPORT_TOOLS = {
    "wg_list_interfaces",
    "wg_list_peers",
    "wg_interface_status",
    "wg_show_interface_config",
    "wg_stats",
    "wg_diagnostics",
}

_declare_report(TOOLS_SCHEMA, _REPORT_TOOLS)

# Per-tool call timeouts (seconds) for work that can exceed the 30s default:
# wg_install_dependencies runs apt-get (budget 300s), wg_setup_server runs
# firewall/NAT commands (budget 300s), and the lifecycle/autostart tools run
# wg-quick (budget 60s). Tools not listed keep the core default.
_TOOL_TIMEOUTS = {
    "wg_install_dependencies": 360,
    "wg_setup_server": 360,
    "wg_interface_start": 90,
    "wg_interface_stop": 90,
    "wg_interface_restart": 90,
    "wg_service_enable": 90,
    "wg_service_disable": 90,
}

_declare_timeouts(TOOLS_SCHEMA, _TOOL_TIMEOUTS)
