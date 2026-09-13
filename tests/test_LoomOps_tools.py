"""Unit tests for the LoomOps plugin tools (no real privileged operations)."""

import json
import os
import signal

import pytest

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
}


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# === schemas / handlers ===

def test_schema_and_handlers_match(load_loomops):
    tools = load_loomops("tools")["tools"]
    names = {tool["name"] for tool in tools.TOOLS_SCHEMA}
    assert names == EXPECTED_TOOLS
    assert set(tools.HANDLERS) == EXPECTED_TOOLS
    assert all(name.startswith("ops_") for name in names)


def test_handler_returns_mcp_shape(load_loomops, tmp_path):
    tools = load_loomops("tools")["tools"]
    result = tools.HANDLERS["ops_disk_usage"]({"path": str(tmp_path)})
    assert result["content"][0]["type"] == "text"
    assert result["data"]["total_bytes"] > 0


# === commands ===

def test_allowed_commands_parses_commas_and_spaces(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "uptime, df ip")
    assert commands.allowed_commands() == {"uptime", "df", "ip"}

    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "")
    assert commands.allowed_commands() == set()


def test_command_timeout_clamps_and_defaults(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_COMMAND_TIMEOUT", "5")
    assert commands.command_timeout() == 5
    monkeypatch.setenv("LOOMOPS_COMMAND_TIMEOUT", "99999")
    assert commands.command_timeout() == commands.MAX_TIMEOUT_SECONDS
    monkeypatch.setenv("LOOMOPS_COMMAND_TIMEOUT", "nonsense")
    assert commands.command_timeout() == commands.DEFAULT_TIMEOUT_SECONDS


def test_run_command_denies_when_allowlist_empty(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.delenv("LOOMOPS_ALLOWED_COMMANDS", raising=False)
    with pytest.raises(ValueError, match="No commands are allowed"):
        commands.run_command({"command": "uptime"})


def test_run_command_denies_unlisted_executable(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "df")
    with pytest.raises(ValueError, match="not allowed"):
        commands.run_command({"command": "uptime -p"})


def test_run_command_rejects_missing_binary(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "uptime")
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="not installed"):
        commands.run_command({"command": "uptime"})


def test_run_command_runs_allowed_and_audits(load_loomops, monkeypatch, tmp_path):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "uptime")
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        commands.subprocess, "run", lambda *a, **k: _Completed(stdout="up 3 days\n")
    )

    result = commands.run_command({"command": "uptime -p"})

    assert result["exit_code"] == 0
    assert result["argv"] == ["uptime", "-p"]
    assert "up 3 days" in result["output"]
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "ops_run_command"
    assert records[0]["argv"] == ["uptime", "-p"]


def test_run_command_accepts_list_form(load_loomops, monkeypatch, tmp_path):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "ip")
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/sbin/{name}")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Completed(stdout="ok")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    commands.run_command({"command": ["ip", "-j", "addr"]})

    assert seen["argv"] == ["ip", "-j", "addr"]


def test_run_command_timeout_is_reported(load_loomops, monkeypatch, tmp_path):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "uptime")
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(*args, **kwargs):
        raise commands.subprocess.TimeoutExpired(cmd="uptime", timeout=1)

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    result = commands.run_command({"command": "uptime"})

    assert result["timed_out"] is True
    assert "timed out" in result["output"]


# === files ===

def test_allowed_paths_parses_separators(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", os.pathsep.join([str(tmp_path), str(other)]))

    roots = files.allowed_paths()

    assert tmp_path.resolve() in roots
    assert other.resolve() in roots


def test_resolve_path_denies_when_unconfigured(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    monkeypatch.delenv("LOOMOPS_ALLOWED_PATHS", raising=False)
    with pytest.raises(ValueError, match="No paths are allowed"):
        files.resolve_path(str(tmp_path))


def test_resolve_path_rejects_outside_roots(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", str(tmp_path))
    with pytest.raises(ValueError, match="outside the allowed paths"):
        files.resolve_path(str(tmp_path.parent / "elsewhere"))


def test_resolve_path_rejects_traversal(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", str(allowed))
    with pytest.raises(ValueError, match="outside the allowed paths"):
        files.resolve_path(str(allowed / ".." / "secret.txt"))


def test_write_then_read_roundtrip(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", str(tmp_path))
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    target = tmp_path / "a.txt"

    written = files.write_file({"path": str(target), "content": "hello"})
    read = files.read_file({"path": str(target)})

    assert target.read_text(encoding="utf-8") == "hello"
    assert written["bytes"] == 5
    assert read["output"] == "hello"
    assert read["size"] == 5
    assert read["truncated"] is False
    assert "ops_write_file" in (tmp_path / "audit.log").read_text(encoding="utf-8")


def test_read_file_truncates_large_files(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", str(tmp_path))
    target = tmp_path / "big.txt"
    target.write_text("x" * (files.MAX_READ_BYTES + 10), encoding="utf-8")

    result = files.read_file({"path": str(target)})

    assert result["truncated"] is True
    assert result["size"] == files.MAX_READ_BYTES + 10


def test_write_file_rejects_directory(load_loomops, monkeypatch, tmp_path):
    files = load_loomops("files")["files"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_PATHS", str(tmp_path))
    target = tmp_path / "adir"
    target.mkdir()
    with pytest.raises(ValueError, match="directory"):
        files.write_file({"path": str(target), "content": "x"})


# === processes ===

def _fake_proc(root, pid, name, state, cmdline):
    base = root / str(pid)
    base.mkdir()
    (base / "status").write_text(f"Name:\t{name}\nState:\t{state}\n", encoding="utf-8")
    (base / "cmdline").write_text(cmdline.replace(" ", "\x00"), encoding="utf-8")


def test_list_processes_sorted_by_pid(load_loomops, monkeypatch, tmp_path):
    processes = load_loomops("processes")["processes"]
    _fake_proc(tmp_path, 20, "nginx", "S (sleeping)", "nginx -g daemon off")
    _fake_proc(tmp_path, 3, "bash", "R (running)", "bash -l")
    monkeypatch.setattr(processes, "PROC_ROOT", tmp_path)

    result = processes.list_processes({})

    assert result["count"] == 2
    assert [row["pid"] for row in result["processes"]] == [3, 20]
    assert result["processes"][0]["name"] == "bash"
    assert result["limited"] is False


def test_list_processes_respects_limit(load_loomops, monkeypatch, tmp_path):
    processes = load_loomops("processes")["processes"]
    _fake_proc(tmp_path, 1, "one", "S", "one")
    _fake_proc(tmp_path, 2, "two", "S", "two")
    monkeypatch.setattr(processes, "PROC_ROOT", tmp_path)

    result = processes.list_processes({"limit": 1})

    assert result["count"] == 1
    assert result["limited"] is True


def test_kill_process_rejects_unsafe_targets(load_loomops):
    processes = load_loomops("processes")["processes"]
    with pytest.raises(ValueError, match="integer"):
        processes.kill_process({"pid": "1"})
    with pytest.raises(ValueError, match="pid <= 1"):
        processes.kill_process({"pid": 1})
    with pytest.raises(ValueError, match="own process"):
        processes.kill_process({"pid": os.getpid()})
    with pytest.raises(ValueError, match="Unsupported signal"):
        processes.kill_process({"pid": 4321, "signal": "USR1"})


def test_kill_process_signals_and_audits(load_loomops, monkeypatch, tmp_path):
    processes = load_loomops("processes")["processes"]
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    killed = []
    monkeypatch.setattr(processes.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = processes.kill_process({"pid": 4321, "signal": "term"})

    assert killed == [(4321, signal.SIGTERM)]
    assert result["signal"] == "TERM"
    assert "ops_kill_process" in (tmp_path / "audit.log").read_text(encoding="utf-8")


def test_kill_process_reports_missing_pid(load_loomops, monkeypatch):
    processes = load_loomops("processes")["processes"]

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(processes.os, "kill", fake_kill)
    with pytest.raises(ValueError, match="No process"):
        processes.kill_process({"pid": 4321})


# === services ===

def _patch_systemd(services, monkeypatch, tmp_path, fake_run):
    marker = tmp_path / "systemd"
    marker.mkdir()
    monkeypatch.setattr(services, "SYSTEMD_MARKER", marker)
    monkeypatch.setattr(services.shutil, "which", lambda name: "/bin/systemctl")
    monkeypatch.setattr(services.subprocess, "run", fake_run)


def test_systemctl_status_reports_active(load_loomops, monkeypatch, tmp_path):
    services = load_loomops("services")["services"]

    def fake_run(command, **kwargs):
        if "is-active" in command:
            return _Completed(stdout="active\n")
        return _Completed(stdout="nginx.service - nginx\n   Active: active (running)")

    _patch_systemd(services, monkeypatch, tmp_path, fake_run)

    result = services.systemctl_status({"service": "nginx"})

    assert result["active"] is True
    assert result["state"] == "active"


def test_systemctl_unavailable_is_actionable(load_loomops, monkeypatch):
    services = load_loomops("services")["services"]
    monkeypatch.setattr(services.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="systemd is not available"):
        services.systemctl_status({"service": "nginx"})


def test_systemctl_restart_success_and_audit(load_loomops, monkeypatch, tmp_path):
    services = load_loomops("services")["services"]
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    _patch_systemd(services, monkeypatch, tmp_path, lambda *a, **k: _Completed())

    result = services.systemctl_restart({"service": "nginx"})

    assert result["service"] == "nginx"
    assert "ops_systemctl_restart" in (tmp_path / "audit.log").read_text(encoding="utf-8")


def test_systemctl_restart_failure_raises(load_loomops, monkeypatch, tmp_path):
    services = load_loomops("services")["services"]
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    _patch_systemd(
        services,
        monkeypatch,
        tmp_path,
        lambda *a, **k: _Completed(returncode=1, stderr="Access denied"),
    )

    with pytest.raises(RuntimeError, match="Access denied"):
        services.systemctl_restart({"service": "nginx"})


def test_service_name_validation(load_loomops):
    services = load_loomops("services")["services"]
    with pytest.raises(ValueError, match="service"):
        services._service({"service": "bad name"})


# === system_info ===

def test_disk_usage_reports_bytes(load_loomops, tmp_path):
    system_info = load_loomops("system_info")["system_info"]
    result = system_info.disk_usage({"path": str(tmp_path)})
    assert result["total_bytes"] > 0
    assert result["free_bytes"] >= 0
    assert "Total:" in result["output"]


def test_disk_usage_missing_path(load_loomops, tmp_path):
    system_info = load_loomops("system_info")["system_info"]
    with pytest.raises(ValueError, match="not found"):
        system_info.disk_usage({"path": str(tmp_path / "nope")})


def test_network_status_parses_proc(load_loomops, monkeypatch, tmp_path):
    system_info = load_loomops("system_info")["system_info"]
    proc = tmp_path / "dev"
    proc.write_text(
        "Interfaces: 1\n face |bytes    packets\n"
        "  eth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(system_info, "PROC_NET_DEV", proc)
    monkeypatch.setattr(system_info.shutil, "which", lambda name: None)

    result = system_info.network_status({})

    assert result["count"] == 1
    interface = result["interfaces"][0]
    assert interface["name"] == "eth0"
    assert interface["receive_bytes"] == 100
    assert interface["transmit_bytes"] == 200


def test_network_status_handles_missing_proc(load_loomops, monkeypatch, tmp_path):
    system_info = load_loomops("system_info")["system_info"]
    monkeypatch.setattr(system_info, "PROC_NET_DEV", tmp_path / "missing")
    monkeypatch.setattr(system_info.shutil, "which", lambda name: None)

    result = system_info.network_status({})

    assert result["count"] == 0
    assert "No network interfaces" in result["output"]


def test_ip_addresses_parses_json(load_loomops, monkeypatch):
    system_info = load_loomops("system_info")["system_info"]
    monkeypatch.setattr(system_info.shutil, "which", lambda name: "/sbin/ip")
    monkeypatch.setattr(
        system_info.subprocess,
        "run",
        lambda *a, **k: _Completed(
            stdout='[{"ifname": "eth0", "addr_info": [{"local": "10.0.0.2"}]}]'
        ),
    )

    assert system_info._ip_addresses() == {"eth0": ["10.0.0.2"]}
