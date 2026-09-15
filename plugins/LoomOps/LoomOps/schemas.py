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
        "description": (
            "List processes from /proc, sorted by pid (host-wide when the plugin "
            "has pid: host)."
        ),
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
    {
        "name": "ops_system_metrics",
        "description": (
            "Read-only host metrics: load average, CPU usage, memory and uptime."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ops_log_search",
        "description": (
            "Search host logs — journald when reachable, otherwise files under "
            "LOOMOPS_LOG_ROOTS. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["auto", "journal", "files"],
                    "default": "auto",
                },
                "query": {"type": "string"},
                "since": {"type": "string", "description": "e.g. '1 hour ago'"},
                "unit": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": [],
        },
    },
    {
        "name": "ops_dmesg",
        "description": (
            "Read the kernel ring buffer. Read-only; may be restricted by "
            "kernel.dmesg_restrict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "default": 200, "maximum": 5000}
            },
            "required": [],
        },
    },
    {
        "name": "ops_failed_units",
        "description": "List failed systemd units. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ops_network_connections",
        "description": "List active network connections with ss. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 200}},
            "required": [],
        },
    },
    {
        "name": "ops_scan_health",
        "description": (
            "Run a read-only host health scan and return a structured report: "
            "failed units, OOM events, high CPU, nearly-full disks, high load and "
            "recent journal errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "default": 24, "maximum": 720},
                "journal_window": {"type": "integer", "default": 60},
                "cpu_threshold": {"type": "number", "default": 80},
                "disk_threshold": {"type": "number", "default": 90},
                "load_per_cpu": {"type": "number", "default": 2.0},
            },
            "required": [],
        },
    },
]

# Per-tool risk declared to the core (Phase 1b). Observation tools are safe;
# anything that mutates the host is dangerous.
_TOOL_RISK = {
    "ops_run_command": "dangerous",
    "ops_write_file": "dangerous",
    "ops_kill_process": "dangerous",
    "ops_systemctl_restart": "dangerous",
    "ops_read_file": "safe",
    "ops_list_processes": "safe",
    "ops_systemctl_status": "safe",
    "ops_disk_usage": "safe",
    "ops_network_status": "safe",
    "ops_system_metrics": "safe",
    "ops_log_search": "safe",
    "ops_dmesg": "safe",
    "ops_failed_units": "safe",
    "ops_network_connections": "safe",
    "ops_scan_health": "safe",
}

for _tool in TOOLS_SCHEMA:
    _tool["risk"] = _TOOL_RISK.get(_tool["name"], "safe")
