"""Plugin test: LoomWG JSON-RPC contract (without calling wg)."""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT_ROOT / "plugins" / "LoomWG" / "main.py"


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


def _by_id(responses: list[dict], *expected_ids: int) -> dict:
    """Index responses by id, failing loudly when any expected id is missing."""
    indexed = {response.get("id"): response for response in responses}
    missing = [request_id for request_id in expected_ids if request_id not in indexed]
    assert not missing, f"missing responses for ids {missing}: {responses}"
    return indexed


def test_initialize_and_tools_list():
    responses = _run_requests(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    by_id = _by_id(responses, 1, 2)

    assert by_id[1]["result"]["protocolVersion"] == "2024-11-05"

    tools = by_id[2]["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"wg_interface_status", "wg_list_peers", "wg_export_existing_clients"} <= names
    assert {
        "wg_show_interface_config",
        "wg_check_interface_config",
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
    assert _by_id(responses, 1)[1]["error"]["code"] == -32601


def test_unknown_tool_returns_error():
    responses = _run_requests(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "nope"},
        }
    )
    assert _by_id(responses, 1)[1]["error"]["code"] == -32601
