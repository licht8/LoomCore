"""Plugin test: LoomOps JSON-RPC contract (no privileged operations)."""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT_ROOT / "plugins" / "LoomOps" / "main.py"

EXPECTED_TOOLS = {
    "ops_run_command",
    "ops_read_file",
    "ops_write_file",
    "ops_list_processes",
    "ops_kill_process",
    "ops_systemctl_status",
    "ops_systemctl_restart",
    "ops_disk_usage",
    "ops_network_status",
    "ops_system_metrics",
    "ops_log_search",
    "ops_dmesg",
    "ops_failed_units",
    "ops_network_connections",
    "ops_scan_health",
}


def _run_requests(*requests: dict, env: dict | None = None) -> list[dict]:
    """Run the plugin, send requests to stdin, and collect the JSON responses."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        env=run_env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_initialize_and_tools_list():
    responses = _run_requests(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )

    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert responses[0]["result"]["serverInfo"]["name"] == "System Operations"

    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == EXPECTED_TOOLS


def test_unknown_method_returns_error():
    responses = _run_requests({"jsonrpc": "2.0", "id": 1, "method": "does/not/exist"})
    assert responses[0]["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    responses = _run_requests(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}}
    )
    assert responses[0]["error"]["code"] == -32601


def test_command_is_denied_without_allowlist():
    responses = _run_requests(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ops_run_command", "arguments": {"command": "uptime"}},
        },
        env={"LOOMOPS_ALLOWED_COMMANDS": ""},
    )
    assert responses[0]["error"]["code"] == -32000
    assert "No commands are allowed" in responses[0]["error"]["message"]


def test_network_status_call_succeeds():
    responses = _run_requests(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ops_network_status", "arguments": {}},
        }
    )
    result = responses[0]["result"]
    assert result["content"][0]["type"] == "text"
    assert "count" in result["data"]
