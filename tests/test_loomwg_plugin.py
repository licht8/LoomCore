"""Plugin test: loomwg JSON-RPC contract (without calling wg)."""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT_ROOT / "plugins" / "loomwg" / "main.py"


def _run_requests(*requests: dict) -> list[dict]:
    """Run the plugin, send requests to stdin, and collect the JSON responses."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
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

    tools = responses[1]["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"wg_status", "wg_list_peers", "wg_export_existing_clients"} <= names
    assert {
        "wg_show_config",
        "wg_check_config",
        "wg_stats",
        "wg_service_enable",
        "wg_service_disable",
    } <= names
    assert "wg_create_client" not in names
    assert "wg_export_client" not in names
    assert "wg_backfill_clients" not in names

    internal = next(t for t in tools if t["name"] == "wg_get_client_artifact")
    assert internal.get("internal") is True


def test_unknown_method_returns_error():
    responses = _run_requests({"jsonrpc": "2.0", "id": 1, "method": "does/not/exist"})
    assert responses[0]["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    responses = _run_requests(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "nope"},
        }
    )
    assert responses[0]["error"]["code"] == -32601
