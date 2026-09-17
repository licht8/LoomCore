"""Tests for the plugin template's JSON-RPC transport resilience."""

import importlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PROJECT_ROOT / "docs" / "architecture" / "plugin-template"
TEMPLATE_MAIN = TEMPLATE_DIR / "main.py"


def _load_template_manifest():
    """Import the template's manifest module by path (no sys.modules clash)."""
    spec = importlib.util.spec_from_file_location(
        "template_manifest", TEMPLATE_DIR / "manifest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_script(script: Path, *requests: object) -> list[dict]:
    """Run a script as a subprocess and collect the JSON responses."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _run_requests(*requests: object) -> list[dict]:
    """Run the template itself as a subprocess."""
    return _run_script(TEMPLATE_MAIN, *requests)


@pytest.fixture
def template_main(monkeypatch):
    """Import the template's main module in-process (dir on sys.path for manifest)."""
    monkeypatch.syspath_prepend(str(TEMPLATE_DIR))
    for name in ("main", "manifest"):
        sys.modules.pop(name, None)
    yield importlib.import_module("main")
    for name in ("main", "manifest"):
        sys.modules.pop(name, None)


def _run_lines(module, monkeypatch, *lines: str) -> list[dict]:
    """Drive main() over one request per line and collect one response per line."""
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    module.main()
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def _drive(module, monkeypatch, *requests: dict) -> list[dict]:
    return _run_lines(
        module, monkeypatch, *(json.dumps(r, ensure_ascii=False) for r in requests)
    )


def test_malformed_requests_keep_process_alive():
    responses = _run_requests(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        [],
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "example_echo", "arguments": "boom"},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
    )

    assert [r["id"] for r in responses] == [1, None, 3, 4]
    assert responses[1]["error"]["code"] == -32600
    assert responses[2]["error"] == {"code": -32602, "message": "Invalid arguments"}
    assert responses[3]["result"]["tools"][0]["name"] == "example_echo"


def test_unexpected_handler_error_survives_subprocess(tmp_path):
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(TEMPLATE_DIR)!r})\n"
        "import main\n"
        "\n"
        "\n"
        "def boom(arguments):\n"
        '    raise TypeError("boom")\n'
        "\n"
        "\n"
        'main.HANDLERS["boom"] = boom\n'
        "main.main()\n",
        encoding="utf-8",
    )
    responses = _run_script(
        wrapper,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )

    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[1]["error"]["code"] == -32603
    assert responses[1]["error"]["message"] == "Internal error"
    assert responses[2]["result"]["tools"][0]["name"] == "example_echo"


def test_unexpected_tool_exception_keeps_process_alive(template_main, monkeypatch):
    def boom(arguments):
        raise TypeError("boom")

    monkeypatch.setitem(template_main.HANDLERS, "boom", boom)
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"
    assert responses[1]["result"]["tools"][0]["name"] == "example_echo"


def test_unserializable_result_is_answered(template_main, monkeypatch):
    monkeypatch.setitem(template_main.HANDLERS, "boom", lambda arguments: {1, 2, 3})
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"
    assert responses[1]["result"]["tools"][0]["name"] == "example_echo"


def test_non_object_payload_is_invalid_request(template_main, monkeypatch):
    responses = _run_lines(
        template_main,
        monkeypatch,
        "[]",
        "5",
        '"x"',
        "null",
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}),
    )

    assert [r["error"]["code"] for r in responses[:4]] == [-32600] * 4
    assert [r["id"] for r in responses[:4]] == [None] * 4
    assert responses[4]["id"] == 9
    assert responses[4]["result"]["tools"][0]["name"] == "example_echo"


def _deep_structure(depth: int = 10000) -> dict:
    value: dict = {}
    for _ in range(depth):
        value = {"a": value}
    return value


def test_deeply_nested_line_is_parse_error(template_main, monkeypatch):
    responses = _run_lines(
        template_main,
        monkeypatch,
        "[" * 10000 + "]" * 10000,
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert responses[1]["result"]["tools"][0]["name"] == "example_echo"


def test_deeply_nested_result_is_answered(template_main, monkeypatch):
    monkeypatch.setitem(
        template_main.HANDLERS, "boom", lambda arguments: _deep_structure()
    )
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"


def test_parse_error_and_unknown_method(template_main, monkeypatch):
    responses = _run_lines(
        template_main,
        monkeypatch,
        "not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert responses[1]["error"]["code"] == -32601
    assert responses[1]["id"] == 7


def test_serialize_falls_back_to_constant_payload(template_main):
    payload = {"jsonrpc": "2.0", "id": _deep_structure(10000), "result": None}

    response = json.loads(template_main._serialize(payload))

    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal error"
    assert response["id"] is None


def test_non_object_params_is_invalid_params(template_main, monkeypatch):
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": ["x"],
        },
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
    )

    assert responses[0]["id"] == 5
    assert responses[0]["error"] == {"code": -32602, "message": "Invalid params"}
    assert responses[1]["result"]["tools"][0]["name"] == "example_echo"


def test_non_object_arguments_is_invalid_arguments(template_main, monkeypatch):
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "example_echo", "arguments": "boom"},
        },
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
    )

    assert responses[0]["id"] == 5
    assert responses[0]["error"] == {"code": -32602, "message": "Invalid arguments"}
    assert responses[1]["result"]["tools"][0]["name"] == "example_echo"


def test_id_less_request_error_uses_null_id(template_main, monkeypatch):
    def boom(arguments):
        raise TypeError("boom")

    monkeypatch.setitem(template_main.HANDLERS, "boom", boom)
    responses = _drive(
        template_main,
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        },
    )

    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32603


def test_template_manifest_flags_malformed_entries():
    module = _load_template_manifest()
    issues = module.validate_manifest(
        {"permissions": {"cap_add": [{"a": 1}], "mounts": [{"target": 5}]}}
    )

    assert any("cap_add entry" in issue for issue in issues)
    assert any("source and target" in issue for issue in issues)
    assert module.check_permissions({"permissions": {"cap_add": [{"a": 1}]}}) == [
        "capability entry {'a': 1} is not a string"
    ]


def test_template_manifest_reports_non_list_containers():
    module = _load_template_manifest()

    assert module.check_permissions({"permissions": {"cap_add": 5}}) == [
        "permissions.cap_add must be a list, got int"
    ]
    assert module.check_permissions({"permissions": {"mounts": True}}) == [
        "permissions.mounts must be a list, got bool"
    ]
