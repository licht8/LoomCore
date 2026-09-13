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
                "interface is down). Keys are masked unless show_secrets is true."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "default": "wg0"},
                    "show_secrets": {"type": "boolean", "default": False},
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
                        "description": "Optional: validate this config file instead of the interface file",
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
