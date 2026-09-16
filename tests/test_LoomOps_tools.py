"""Unit tests for the LoomOps plugin tools (no real privileged operations)."""

import io
import json
import os
import signal
import sys

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
    "ops_system_metrics",
    "ops_log_search",
    "ops_dmesg",
    "ops_failed_units",
    "ops_network_connections",
    "ops_scan_health",
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


def test_run_command_rejects_path_qualified_name(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "ls")
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/bin/{name}")
    calls = []
    monkeypatch.setattr(
        commands.subprocess, "run", lambda *a, **k: calls.append(a) or _Completed()
    )

    for command in ("./ls", "/bin/ls", "../bin/ls"):
        with pytest.raises(ValueError, match="by name, not by path"):
            commands.run_command({"command": command})

    assert calls == []


def test_run_command_executes_resolved_absolute_path(load_loomops, monkeypatch, tmp_path):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "uptime")
    monkeypatch.setenv("LOOMOPS_AUDIT_LOG", str(tmp_path / "audit.log"))
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Completed(stdout="up 3 days\n")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    result = commands.run_command({"command": "uptime -p"})

    assert seen["argv"] == ["/usr/bin/uptime", "-p"]
    assert result["argv"] == ["/usr/bin/uptime", "-p"]


def test_run_command_denies_allowlisted_interpreter(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "sh")
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/bin/{name}")

    with pytest.raises(ValueError, match="arbitrary code"):
        commands.run_command({"command": "sh -c 'id'"})


def test_run_command_denies_allowlisted_python_list_form(load_loomops, monkeypatch):
    commands = load_loomops("commands")["commands"]
    monkeypatch.setenv("LOOMOPS_ALLOWED_COMMANDS", "python3")
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/usr/bin/{name}")

    with pytest.raises(ValueError, match="arbitrary code"):
        commands.run_command({"command": ["python3", "-c", "print(1)"]})


def test_denied_commands_include_common_shell_escapes(load_loomops):
    commands = load_loomops("commands")["commands"]
    for name in ("bash", "env", "xargs", "sudo", "find", "timeout"):
        assert name in commands.DENIED_COMMANDS


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
    assert result["argv"] == ["/usr/bin/uptime", "-p"]
    assert "up 3 days" in result["output"]
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "ops_run_command"
    assert records[0]["argv"] == ["/usr/bin/uptime", "-p"]


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

    assert seen["argv"] == ["/sbin/ip", "-j", "addr"]


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


def test_service_name_rejects_option_like_value(load_loomops):
    services = load_loomops("services")["services"]
    with pytest.raises(ValueError, match="option"):
        services._service({"service": "--all"})


def test_service_name_rejects_shell_metacharacters(load_loomops):
    services = load_loomops("services")["services"]
    with pytest.raises(ValueError, match="invalid characters"):
        services._service({"service": "nginx;malicious"})


def test_service_name_accepts_instanced_unit(load_loomops):
    services = load_loomops("services")["services"]
    assert services._service({"service": "nginx@foo.service"}) == "nginx@foo.service"


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


# === monitoring ===

def _fake_host_proc(root, init_name="systemd"):
    (root / "1").mkdir(parents=True, exist_ok=True)
    (root / "1" / "comm").write_text(f"{init_name}\n", encoding="utf-8")


def test_host_pid_active(load_loomops, monkeypatch, tmp_path):
    monitoring = load_loomops("monitoring")["monitoring"]
    _fake_host_proc(tmp_path)
    monkeypatch.setattr(monitoring, "PROC_ROOT", tmp_path)
    assert monitoring.host_pid_active() is True


def test_host_pid_inactive(load_loomops, monkeypatch, tmp_path):
    monitoring = load_loomops("monitoring")["monitoring"]
    _fake_host_proc(tmp_path, "uvicorn")
    monkeypatch.setattr(monitoring, "PROC_ROOT", tmp_path)
    assert monitoring.host_pid_active() is False


def test_system_metrics_shape(load_loomops, monkeypatch, tmp_path):
    monitoring = load_loomops("monitoring")["monitoring"]
    (tmp_path / "loadavg").write_text("0.50 0.40 0.30 1/100 1234\n", encoding="utf-8")
    (tmp_path / "uptime").write_text("12345.6 999.0\n", encoding="utf-8")
    (tmp_path / "meminfo").write_text(
        "MemTotal: 1000 kB\nMemAvailable: 400 kB\n", encoding="utf-8"
    )
    (tmp_path / "stat").write_text("cpu 1 2 3 4 5 6 7 8 9 10\n", encoding="utf-8")
    monkeypatch.setattr(monitoring, "PROC_ROOT", tmp_path)
    monkeypatch.setattr(monitoring, "cpu_percent", lambda: 12.5)

    result = monitoring.system_metrics({})

    assert result["cpu_percent"] == 12.5
    assert result["memory_total_kb"] == 1000
    assert result["memory_used_kb"] == 600
    assert result["uptime_seconds"] == 12345.6
    assert result["load_per_cpu"] is not None
    assert "CPU:" in result["output"]


def test_dmesg_truncates(load_loomops, monkeypatch):
    monitoring = load_loomops("monitoring")["monitoring"]
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: "/bin/dmesg")
    monkeypatch.setattr(
        monitoring.subprocess, "run", lambda *a, **k: _Completed(stdout="a\nb\nc\n")
    )

    result = monitoring.dmesg({"lines": 2})

    assert result["entries"] == ["b", "c"]
    assert result["truncated"] is True


def test_dmesg_missing_binary(load_loomops, monkeypatch):
    monitoring = load_loomops("monitoring")["monitoring"]
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not installed"):
        monitoring.dmesg({})


def test_network_connections_shape(load_loomops, monkeypatch):
    monitoring = load_loomops("monitoring")["monitoring"]
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: "/usr/bin/ss")
    monkeypatch.setattr(
        monitoring.subprocess,
        "run",
        lambda *a, **k: _Completed(stdout="tcp ESTAB 0 0 1.2.3.4:22 5.6.7.8:5\n"),
    )

    result = monitoring.network_connections({})

    assert result["count"] == 1
    assert "tcp ESTAB" in result["output"]


# === logs ===

def test_log_search_files_fallback(load_loomops, monkeypatch, tmp_path):
    logs = load_loomops("logs")["logs"]
    (tmp_path / "syslog").write_text("hello\nERROR boom\n", encoding="utf-8")
    monkeypatch.setenv("LOOMOPS_LOG_ROOTS", str(tmp_path))
    monkeypatch.setattr(logs, "journal_available", lambda: False)

    result = logs.log_search({"query": "error"})

    assert result["source"] == "files"
    assert result["count"] == 1
    assert "boom" in result["output"]


def test_log_search_journal(load_loomops, monkeypatch):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs, "journal_available", lambda: True)
    monkeypatch.setattr(logs, "_run_journal", lambda args: "line one\nline two\n")

    result = logs.log_search({"source": "journal", "query": "x"})

    assert result["source"] == "journal"
    assert result["count"] == 2


def test_log_search_journal_unavailable_raises(load_loomops, monkeypatch):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs, "journal_available", lambda: False)
    with pytest.raises(RuntimeError, match="journald is not available"):
        logs.log_search({"source": "journal"})


def test_journal_errors_files_fallback(load_loomops, monkeypatch, tmp_path):
    logs = load_loomops("logs")["logs"]
    (tmp_path / "app.log").write_text("all good\ncritical failure\n", encoding="utf-8")
    monkeypatch.setenv("LOOMOPS_LOG_ROOTS", str(tmp_path))
    monkeypatch.setattr(logs, "journal_available", lambda: False)

    result = logs.journal_errors({})

    assert result["source"] == "files"
    assert result["count"] == 1


def test_max_log_lines_clamps(load_loomops, monkeypatch):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setenv("LOOMOPS_MAX_LOG_LINES", "99999")
    assert logs.max_log_lines() == logs.MAX_LINES


def test_journal_available_with_mounted_directory(load_loomops, monkeypatch, tmp_path):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs.shutil, "which", lambda name: "/usr/bin/journalctl")
    machine = tmp_path / "machine-id"
    machine.mkdir()
    (machine / "system.journal").write_text("", encoding="utf-8")
    monkeypatch.setenv("LOOMOPS_JOURNAL_DIRS", str(tmp_path))

    assert logs.journal_available() is True


def test_journal_available_false_when_directory_empty(
    load_loomops, monkeypatch, tmp_path
):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs.shutil, "which", lambda name: "/usr/bin/journalctl")
    monkeypatch.setenv("LOOMOPS_JOURNAL_DIRS", str(tmp_path))

    assert logs.journal_available() is False


def test_journal_lines_ignores_no_entries_marker(load_loomops):
    logs = load_loomops("logs")["logs"]

    assert logs._journal_lines("-- No entries --\n\n") == []
    assert logs._journal_lines("real line\n") == ["real line"]


def test_journal_errors_ignores_no_entries(load_loomops, monkeypatch):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs, "journal_available", lambda: True)
    monkeypatch.setattr(logs, "_run_journal", lambda args: "-- No entries --\n")

    result = logs.journal_errors({})

    assert result["count"] == 0
    assert result["entries"] == []


def test_run_journal_reads_the_mounted_directory(load_loomops, monkeypatch, tmp_path):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs.shutil, "which", lambda name: "/usr/bin/journalctl")
    (tmp_path / "system.journal").write_text("", encoding="utf-8")
    monkeypatch.setenv("LOOMOPS_JOURNAL_DIRS", str(tmp_path))
    captured: dict[str, list[str]] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _Completed(stdout="entry one\n")

    monkeypatch.setattr(logs.subprocess, "run", fake_run)

    result = logs._search_journal(query="x", since="", unit="", limit=10)

    assert captured["argv"][:3] == [
        "/usr/bin/journalctl",
        "--no-pager",
        "--directory",
    ]
    assert str(tmp_path) in captured["argv"]
    assert result["count"] == 1


def test_run_journal_without_journal_files_raises(load_loomops, monkeypatch, tmp_path):
    logs = load_loomops("logs")["logs"]
    monkeypatch.setattr(logs.shutil, "which", lambda name: "/usr/bin/journalctl")
    monkeypatch.setenv("LOOMOPS_JOURNAL_DIRS", str(tmp_path / "absent"))

    with pytest.raises(RuntimeError, match="no journal files"):
        logs._search_journal(query="", since="", unit="", limit=5)


# === diagnostics ===

def _patch_quiet_health(diagnostics, monkeypatch, tmp_path):
    monitoring = diagnostics.monitoring
    monkeypatch.setattr(diagnostics, "failed_units", lambda: [])
    monkeypatch.setattr(monitoring, "host_pid_active", lambda: True)
    monkeypatch.setattr(monitoring, "cpu_percent", lambda: 1.0)
    monkeypatch.setattr(monitoring, "load_average", lambda: (0.1, 0.1, 0.1))
    monkeypatch.setattr(monitoring, "dmesg", lambda args: {"entries": []})
    monkeypatch.setattr(diagnostics.logs, "journal_available", lambda: True)
    monkeypatch.setattr(diagnostics.logs, "_run_journal", lambda args: "")
    monkeypatch.setattr(
        diagnostics.logs,
        "journal_errors",
        lambda args: {"entries": [], "source": "journal"},
    )
    monkeypatch.setattr(diagnostics.logs, "log_roots", lambda: [tmp_path])
    monkeypatch.setattr(diagnostics, "_read_mounts", lambda: [])


def test_scan_health_healthy_report(load_loomops, monkeypatch, tmp_path):
    diagnostics = load_loomops("diagnostics")["diagnostics"]
    _patch_quiet_health(diagnostics, monkeypatch, tmp_path)

    result = diagnostics.scan_health({})

    assert result["summary"]["status"] == "healthy"
    assert result["host_access"]["pid_namespace_host"] is True
    assert result["thresholds"]["hours"] == 24
    assert "Health scan: healthy" in result["output"]


def test_scan_health_reports_missing_access(load_loomops, monkeypatch, tmp_path):
    diagnostics = load_loomops("diagnostics")["diagnostics"]
    monitoring = diagnostics.monitoring
    monkeypatch.setattr(diagnostics, "failed_units", lambda: ["nginx.service"])
    monkeypatch.setattr(monitoring, "host_pid_active", lambda: False)
    monkeypatch.setattr(monitoring, "cpu_percent", lambda: None)
    monkeypatch.setattr(monitoring, "load_average", lambda: None)
    monkeypatch.setattr(monitoring, "dmesg", lambda args: {"entries": []})
    monkeypatch.setattr(diagnostics.logs, "journal_available", lambda: False)
    monkeypatch.setattr(
        diagnostics.logs,
        "journal_errors",
        lambda args: {"entries": [], "source": "files"},
    )
    monkeypatch.setattr(diagnostics.logs, "log_roots", lambda: [tmp_path / "missing"])
    monkeypatch.setattr(diagnostics, "_read_mounts", lambda: None)

    result = diagnostics.scan_health({})

    categories = {finding["category"] for finding in result["findings"]}
    assert "service" in categories
    assert result["summary"]["status"] == "critical"
    missing = result["host_access"]["missing"]
    assert any("PID namespace" in note for note in missing)
    assert any("journald" in note for note in missing)
    assert any("/proc/mounts" in note for note in missing)


def test_failed_units_handler(load_loomops, monkeypatch, tmp_path):
    services = load_loomops("services")["services"]
    _patch_systemd(
        services,
        monkeypatch,
        tmp_path,
        lambda *a, **k: _Completed(stdout="nginx.service loaded failed failed\n"),
    )

    result = services.failed_units_handler({})

    assert result["units"] == ["nginx.service"]
    assert result["count"] == 1


# === protocol.py: transport resilience ===

def _run_protocol(protocol, monkeypatch, handlers, *lines: str) -> list[dict]:
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    protocol.run(handlers, [])
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_protocol_answers_unexpected_tool_exception(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]

    def boom(arguments):
        raise KeyError("boom")

    responses = _run_protocol(
        protocol,
        monkeypatch,
        {"boom": boom},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    )

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"
    assert responses[1]["result"] == {"tools": []}


def test_protocol_answers_unserializable_result(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {"boom": lambda arguments: {1, 2, 3}},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        ),
    )

    assert responses[0]["id"] == 5
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"


def test_protocol_non_object_payload_is_invalid_request(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        "[]",
        "5",
        '"x"',
        "null",
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}),
    )

    assert [r["error"]["code"] for r in responses[:4]] == [-32600] * 4
    assert [r["id"] for r in responses[:4]] == [0] * 4
    assert responses[4]["id"] == 9
    assert responses[4]["result"] == {"tools": []}


def _deep_structure(depth: int = 10000) -> dict:
    value: dict = {}
    for _ in range(depth):
        value = {"a": value}
    return value


def test_protocol_deeply_nested_line_is_parse_error(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        "[" * 10000 + "]" * 10000,
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] == 0
    assert responses[1]["result"] == {"tools": []}


def test_protocol_deeply_nested_result_is_answered(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {"boom": lambda arguments: _deep_structure()},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    )

    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["error"]["code"] == -32603
    assert responses[0]["error"]["message"] == "Internal error"


def test_protocol_parse_error_and_unknown_method(load_loomops, monkeypatch):
    protocol = load_loomops("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        "not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] == 0
    assert responses[1]["error"]["code"] == -32601
    assert responses[1]["id"] == 7


def test_protocol_serialize_falls_back_to_constant_payload(load_loomops):
    protocol = load_loomops("protocol")["protocol"]
    payload = {"jsonrpc": "2.0", "id": _deep_structure(10000), "result": None}

    response = json.loads(protocol._serialize(payload))

    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal error"
    assert response["id"] is None
