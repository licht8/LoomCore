"""MCP tool schemas advertised by the LoomOps plugin."""

from __future__ import annotations

TOOLS_SCHEMA = [
    {
        "name": "ops_run_command",
        "description": (
            "Run an allow-listed command without a shell. The executable basename "
            "must be listed in LOOMOPS_ALLOWED_COMMANDS (empty denies all). "
            "Arguments are split with shlex; output is captured and capped. "
            "Requires admin approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command line, e.g. 'uptime -p' or 'df -h'",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "ops_read_file",
        "description": (
            "Read a text file inside LOOMOPS_ALLOWED_PATHS (empty denies all). "
            "Output is capped at 256 KB."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "ops_write_file",
        "description": (
            "Atomically write a text file inside LOOMOPS_ALLOWED_PATHS (empty "
            "denies all). The action is recorded in the audit log. Requires admin "
            "approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "ops_list_processes",
        "description": "List processes visible to the container from /proc, sorted by pid.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 200,
                    "minimum": 1,
                    "maximum": 2000,
                }
            },
            "required": [],
        },
    },
    {
        "name": "ops_kill_process",
        "description": (
            "Send a signal (TERM, KILL, INT or HUP) to a single pid and record it "
            "in the audit log. Requires admin approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {"type": "integer"},
                "signal": {
                    "type": "string",
                    "enum": ["TERM", "KILL", "INT", "HUP"],
                    "default": "TERM",
                },
            },
            "required": ["pid"],
        },
    },
    {
        "name": "ops_systemctl_status",
        "description": (
            "Show the status of a systemd unit. Returns an actionable error when "
            "systemd is not available in the container."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
    },
    {
        "name": "ops_systemctl_restart",
        "description": (
            "Restart a systemd unit and record it in the audit log. Requires admin "
            "approval; returns an actionable error when systemd is unavailable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
    },
    {
        "name": "ops_disk_usage",
        "description": "Report total, used and free disk space for a path (default '/').",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "/"}},
            "required": [],
        },
    },
    {
        "name": "ops_network_status",
        "description": "List network interfaces with traffic counters and addresses.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]
