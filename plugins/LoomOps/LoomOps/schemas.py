"""MCP tool schemas advertised by the LoomOps plugin."""

from __future__ import annotations

TOOLS_SCHEMA = [
    {
        "name": "ops_run_command",
        "description": (
            "Run an allow-listed command without a shell. The executable basename "
            "must be listed in LOOMOPS_ALLOWED_COMMANDS (empty denies all). "
            "Provide a shell-quoted string or an argv list. Arguments are split "
            "with shlex; output is captured and capped. Requires admin approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": (
                        "Command line (shell-quoted string) or an argv list, "
                        "e.g. 'uptime -p' or ['df', '-h']"
                    ),
                }
            },
            "required": ["command"],
        },
        "timeout": 600,
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
            "approval. Content is capped at 256 KB."
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
        "timeout": 60,
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
        "timeout": 60,
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
            "LOOMOPS_LOG_ROOTS. 'journald' is accepted as an alias for 'journal'. "
            "Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["auto", "journal", "journald", "files"],
                    "default": "auto",
                },
                "query": {"type": "string"},
                "since": {"type": "string", "description": "e.g. '1 hour ago'"},
                "unit": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": [],
        },
        "timeout": 60,
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
        "timeout": 60,
    },
    {
        "name": "ops_failed_units",
        "description": "List failed systemd units. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "timeout": 60,
    },
    {
        "name": "ops_network_connections",
        "description": "List active network connections with ss. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 200}},
            "required": [],
        },
        "timeout": 60,
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
        "timeout": 120,
    },
]

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


def _declare_report(
    tools: list[dict], report: set[str], report_arg: dict[str, str] | None = None
) -> None:
    """Mark report tools; refuse stale names.

    ``report`` names carry ``report: true``. ``report_arg`` maps a tool name to
    the argument whose read-only value makes that tool a report. Both are
    opt-in, so tools not listed stay untouched, but a declared name without a
    matching tool means the declaration went stale and is an error.
    """
    names = {tool["name"] for tool in tools}
    declared = set(report) | set(report_arg or {})
    unknown = sorted(declared - names)
    if unknown:
        raise RuntimeError(
            f"Report declarations reference unknown tools: {unknown}"
        )
    for tool in tools:
        if tool["name"] in report:
            tool["report"] = True
        elif report_arg and tool["name"] in report_arg:
            tool["report_arg"] = report_arg[tool["name"]]


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

_declare_risk(TOOLS_SCHEMA, _TOOL_RISK)

# Read-only listings/reports whose raw output already is the answer: the turn
# skips the summary LLM call. ``ops_run_command`` is a report only when its
# command is allow-listed as read-only (see LOOMCORE_REPORT_COMMANDS).
_REPORT_TOOLS = {
    "ops_list_processes",
    "ops_failed_units",
    "ops_scan_health",
    "ops_network_status",
    "ops_dmesg",
    "ops_disk_usage",
    "ops_system_metrics",
}

_declare_report(
    TOOLS_SCHEMA, _REPORT_TOOLS, {"ops_run_command": "command"}
)
