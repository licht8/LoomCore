"""Unit tests for LoomWG plugin logic without real WireGuard calls."""

import base64
import time
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

import pytest


def _fake_key(seed: int) -> str:
    """A WireGuard-shaped public key (base64 of 32 bytes)."""
    return base64.b64encode(bytes([seed] * 32)).decode()


# === client.py: pure functions ===

def test_format_endpoint_ipv4(load_loomwg):
    client = load_loomwg("client")["client"]
    assert client._format_endpoint("1.2.3.4", 51820) == "1.2.3.4:51820"


def test_format_endpoint_ipv6(load_loomwg):
    client = load_loomwg("client")["client"]
    assert client._format_endpoint("fd42::1", 51820) == "[fd42::1]:51820"


def test_safe_name(load_loomwg):
    clients_store = load_loomwg("clients_store")["clients_store"]
    assert clients_store.validate_name("good-name_1") == "good-name_1"
    with pytest.raises(ValueError):
        clients_store.validate_name("bad name")


def test_client_addresses_explicit(load_loomwg):
    client = load_loomwg("client")["client"]
    result = client._client_addresses(
        {"addresses": ["10.0.0.2/32"]}, "wg0", ["10.0.0.0/24"], ["10.0.0.1"]
    )
    assert result == ["10.0.0.2/32"]


def test_client_addresses_rejects_default_route(load_loomwg):
    client = load_loomwg("client")["client"]
    with pytest.raises(ValueError):
        client._client_addresses({"addresses": ["0.0.0.0/0"]}, "wg0", [], [])


def test_allocate_addresses(load_loomwg, monkeypatch):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())

    result = client._allocate_addresses("wg0", ["10.66.66.0/24"], ["10.66.66.1"])

    assert result == ["10.66.66.2/32"]


def test_get_artifact_by_name(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    conf_dir = tmp_path / "clients" / "conf"
    conf_dir.mkdir(parents=True)
    (conf_dir / "alice.conf").write_bytes(b"[Interface]\n")
    client.clients_store.upsert(
        "alice",
        {
            "public_key": _fake_key(1),
            "private_key": _fake_key(2),
            "addresses": ["10.0.0.2/32"],
        },
    )

    result = client.get_artifact({"name": "alice", "kind": "config"})

    assert result["filename"] == "alice.conf"
    assert base64.b64decode(result["content_base64"]) == b"[Interface]\n"
    # Artifacts are durable — the same name can be fetched again.
    again = client.get_artifact({"name": "alice", "kind": "config"})
    assert again["filename"] == "alice.conf"


def test_get_artifact_missing_key_errors(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert("ghost", {"public_key": _fake_key(3), "addresses": []})

    with pytest.raises(ValueError, match="rotate=true"):
        client.get_artifact({"name": "ghost", "kind": "config"})


# === config.py: peer persistence ===

def test_add_and_remove_peer(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    config_file = tmp_path / "wg0.conf"
    config_file.write_text("[Interface]\nPrivateKey = x\n", encoding="utf-8")
    public_key = _fake_key(7)

    config.add_peer("wg0", public_key, ["10.0.0.2/32"], None)
    assert public_key in config_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        config.add_peer("wg0", public_key, ["10.0.0.2/32"], None)

    config.remove_peer("wg0", public_key)
    assert public_key not in config_file.read_text(encoding="utf-8")


def test_add_peer_rejects_bad_key(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    (tmp_path / "wg0.conf").write_text("[Interface]\n", encoding="utf-8")

    with pytest.raises(ValueError):
        config.add_peer("wg0", "not-a-key", ["10.0.0.2/32"], None)


# === provision.py: LOOMWG_SKIP_FIREWALL ===

def _patch_setup_server(provision, monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "config_path", lambda interface: tmp_path / f"{interface}.conf")
    monkeypatch.setattr(provision, "run_command", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(
        provision, "generate_keypair", lambda: {"private_key": "priv", "public_key": "pub"}
    )
    monkeypatch.setattr(
        provision,
        "detect_host",
        lambda: {"os": "rocky 10", "external_interface": "eth0", "firewall": "firewalld"},
    )
    monkeypatch.setattr(provision, "enable_forwarding", lambda: None)


def test_setup_server_skips_firewall(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)
    monkeypatch.setenv("LOOMWG_SKIP_FIREWALL", "1")
    called = {"firewall": False}

    def fake_configure(*args, **kwargs):
        called["firewall"] = True
        return "firewalld"

    monkeypatch.setattr(provision, "configure_firewall", fake_configure)

    result = provision.setup_server({"interface": "wgtest"})

    assert result["firewall"] == "skipped"
    assert called["firewall"] is False
    assert (tmp_path / "wgtest.conf").is_file()


def test_setup_server_configures_firewall_by_default(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "firewalld")

    result = provision.setup_server({"interface": "wgtest"})

    assert result["firewall"] == "firewalld"


# === dependencies.py: dependency installation (in-container) ===

def test_dependency_report_all_present(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    report = install.dependency_report()

    assert report["ready"] is True
    assert report["missing"] == []
    assert report["package_manager"] == "apt"


def test_ensure_dependencies_noop_when_present(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    called = {"install": False}
    monkeypatch.setattr(
        install,
        "run_shell",
        lambda *args, **kwargs: called.__setitem__("install", True),
    )

    result = install.ensure_dependencies()

    assert result["changed"] is False
    assert called["install"] is False


def test_ensure_dependencies_installs_missing(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    state = {"missing": "wg"}

    def fake_which(tool):
        return None if tool == state["missing"] else f"/usr/bin/{tool}"

    monkeypatch.setattr(install.shutil, "which", fake_which)
    calls: list[str] = []

    def fake_shell(command, timeout=300):
        calls.append(command)
        state["missing"] = None
        return ""

    monkeypatch.setattr(install, "run_shell", fake_shell)

    result = install.ensure_dependencies()

    assert result["changed"] is True
    assert result["package_manager"] == "apt"
    assert result["missing_before"] == ["wg"]
    assert "wireguard-tools" in calls[0]
    assert result["missing_after"] == []


def test_ensure_dependencies_requires_package_manager(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: None)

    with pytest.raises(RuntimeError, match="no supported package manager"):
        install.ensure_dependencies()


def test_ensure_kernel_module_reports_host_hint(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(install, "_kernel_support_available", lambda: False)

    result = install.ensure_kernel_module()

    assert result["loaded"] is False
    assert "kmod-wireguard" in result["message"]


def test_ensure_kernel_module_available(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(install, "_kernel_support_available", lambda: True)

    result = install.ensure_kernel_module()

    assert result["loaded"] is True


def test_install_wireguard_returns_ready(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(
        install,
        "ensure_dependencies",
        lambda: {"changed": False, "message": "deps ok"},
    )
    monkeypatch.setattr(
        install,
        "ensure_kernel_module",
        lambda: {"loaded": True, "message": "module ok"},
    )

    result = install.install_wireguard({})

    assert result["data"]["ready"] is True
    assert "deps ok" in result["content"][0]["text"]


# === wireguard.py: parsers ===

def test_parse_transfer_bytes(load_loomwg):
    wg = load_loomwg("wireguard")["wireguard"]
    assert wg._parse_transfer_bytes("1.5 KiB") == 1536
    assert wg._parse_transfer_bytes("2 MiB") == 2 * 1024**2
    assert wg._parse_transfer_bytes("bogus") == 0


def test_peer_is_online(load_loomwg):
    wg = load_loomwg("wireguard")["wireguard"]
    recent = datetime.now(tz=UTC).isoformat()
    assert wg.peer_is_online({"latest_handshake": recent}) is True
    assert wg.peer_is_online({"latest_handshake": None}) is False


def test_validate_interface(load_loomwg):
    wg = load_loomwg("wireguard")["wireguard"]
    assert wg.validate_interface({"interface": "wg0"}) == "wg0"
    with pytest.raises(ValueError):
        wg.validate_interface({"interface": "bad iface!"})


# === wireguard.py: read_interface (wg show dump, 8 fields per peer) ===

def _dump_output(
    handshake_ts: int,
    keepalive: str = "25",
    allowed_ips: str = "10.66.66.4/32,fd42:42:42::4/128",
) -> str:
    interface = "wPrivKey== wServerPub== 51820 off"
    peer = (
        "wPeerPub== (none) 91.90.160.72:51361 "
        f"{allowed_ips} "
        f"{handshake_ts} 6438256 96416563 {keepalive}"
    )
    return f"{interface}\n{peer}"


def test_read_interface_parses_dump(load_loomwg, monkeypatch):
    wg = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(wg, "run_command", lambda *args, **kwargs: _dump_output(int(time.time())))

    details = wg.read_interface("wg0")

    assert details["public_key"] == "wServerPub=="
    assert details["listen_port"] == 51820
    assert len(details["peers"]) == 1

    peer = details["peers"][0]
    assert peer["endpoint"] == "91.90.160.72:51361"
    assert peer["allowed_ips"] == ["10.66.66.4/32", "fd42:42:42::4/128"]
    assert peer["latest_handshake"] is not None
    assert peer["receive_bytes"] == 6438256
    assert peer["transmit_bytes"] == 96416563
    assert peer["persistent_keepalive"] == 25
    assert wg.peer_is_online(peer) is True


def test_read_interface_offline_without_handshake(load_loomwg, monkeypatch):
    wg = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(wg, "run_command", lambda *args, **kwargs: _dump_output(0))

    peer = wg.read_interface("wg0")["peers"][0]

    assert peer["latest_handshake"] is None
    assert wg.peer_is_online(peer) is False


def test_read_interface_handles_off_keepalive(load_loomwg, monkeypatch):
    wg = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(
        wg, "run_command", lambda *args, **kwargs: _dump_output(int(time.time()), keepalive="off")
    )

    peer = wg.read_interface("wg0")["peers"][0]

    assert peer["persistent_keepalive"] == 0
    assert wg.peer_is_online(peer) is True


def test_read_interface_handles_none_allowed_ips(load_loomwg, monkeypatch):
    wg = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(
        wg, "run_command", lambda *args, **kwargs: _dump_output(0, allowed_ips="(none)")
    )

    peer = wg.read_interface("wg0")["peers"][0]

    assert peer["allowed_ips"] == []


# === device.py: run_command / has_link / configured_interfaces ===

class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_command_raises_on_failure(load_loomwg, monkeypatch):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(returncode=2, stderr="boom")
    )

    with pytest.raises(device.CommandError, match="boom"):
        device.run_command(["wg", "show"])


def test_run_command_wraps_oserror(load_loomwg, monkeypatch):
    device = load_loomwg("wireguard")["wireguard"]

    def explode(*args, **kwargs):
        raise OSError("wg missing")

    monkeypatch.setattr(device.subprocess, "run", explode)

    with pytest.raises(device.CommandError, match="wg missing"):
        device.run_command(["wg"])


def test_run_command_strips_output(load_loomwg, monkeypatch):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(stdout="  hi \n")
    )

    assert device.run_command(["wg"]) == "hi"


def test_has_link_true_and_false(load_loomwg, monkeypatch):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(device, "run_command", lambda *a, **k: "1: wg0")
    assert device.has_link("wg0") is True

    def missing(*args, **kwargs):
        raise device.CommandError("not found")

    monkeypatch.setattr(device, "run_command", missing)
    assert device.has_link("wg0") is False


def test_configured_interfaces_filters_names(load_loomwg, monkeypatch, tmp_path):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path)
    (tmp_path / "wg0.conf").write_text("", encoding="utf-8")
    (tmp_path / "bad name.conf").write_text("", encoding="utf-8")

    assert device.configured_interfaces() == ["wg0"]


def test_configured_interfaces_missing_dir(load_loomwg, monkeypatch, tmp_path):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path / "nope")

    assert device.configured_interfaces() == []


def test_config_directory_exposes_config_dir(load_loomwg, monkeypatch, tmp_path):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path)

    assert device.config_directory() == tmp_path


# === tools.list_interfaces: `wg show interfaces` must be reported ===

def test_list_interfaces_reports_active_interface(load_loomwg, monkeypatch, tmp_path):
    modules = load_loomwg("wireguard", "tools")
    device, tools = modules["wireguard"], modules["tools"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(stdout="wg0\n")
    )

    result = tools.list_interfaces({})

    assert "wg0" in result["content"][0]["text"]
    assert result["data"]["interfaces"] == [
        {"name": "wg0", "active": True, "configured": False}
    ]


def test_list_interfaces_reports_multiple_active(load_loomwg, monkeypatch, tmp_path):
    modules = load_loomwg("wireguard", "tools")
    device, tools = modules["wireguard"], modules["tools"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(stdout="wg0 wg1\n")
    )

    names = [entry["name"] for entry in tools.list_interfaces({})["data"]["interfaces"]]

    assert names == ["wg0", "wg1"]


def test_list_interfaces_does_not_swallow_wg_failure(load_loomwg, monkeypatch, tmp_path):
    modules = load_loomwg("wireguard", "tools")
    device, tools = modules["wireguard"], modules["tools"]
    monkeypatch.setattr(device, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: _Completed(returncode=1, stderr="wg: command not found"),
    )

    with pytest.raises(device.CommandError, match="command not found"):
        tools.list_interfaces({})


def test_active_interfaces_logs_empty_result(load_loomwg, monkeypatch, caplog):
    device = load_loomwg("wireguard")["wireguard"]
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(stdout="")
    )

    with caplog.at_level("INFO", logger="LoomWG.wg"):
        assert device.active_interfaces() == []

    assert any("no active" in record.message.lower() for record in caplog.records)


# === keys.py ===

def test_validate_public_key_strips_and_accepts(load_loomwg):
    keys = load_loomwg("keys")["keys"]
    key = _fake_key(5)

    assert keys.validate_public_key(f"  {key}  ") == key


def test_validate_public_key_rejects_non_base64(load_loomwg):
    keys = load_loomwg("keys")["keys"]

    with pytest.raises(ValueError, match="Invalid WireGuard public key"):
        keys.validate_public_key("not-a-key")


def test_validate_public_key_rejects_wrong_length(load_loomwg):
    keys = load_loomwg("keys")["keys"]
    short = base64.b64encode(b"short").decode()

    with pytest.raises(ValueError, match="32 bytes"):
        keys.validate_public_key(short)


def test_generate_keypair(load_loomwg, monkeypatch):
    keys = load_loomwg("keys")["keys"]

    def fake_run(command, timeout=15, input_text=None, *, sensitive=False):
        return "PRIV" if command[1] == "genkey" else "PUB"

    monkeypatch.setattr(keys, "run_command", fake_run)

    assert keys.generate_keypair() == {"private_key": "PRIV", "public_key": "PUB"}


def test_generate_keypair_empty_raises(load_loomwg, monkeypatch):
    keys = load_loomwg("keys")["keys"]
    monkeypatch.setattr(keys, "run_command", lambda *a, **k: "")

    with pytest.raises(keys.CommandError):
        keys.generate_keypair()


def test_generate_keypair_redacts_private_key_from_logs(load_loomwg, monkeypatch, caplog):
    modules = load_loomwg("keys", "wireguard")
    keys, device = modules["keys"], modules["wireguard"]
    secret = "SECRET-PRIVATE-KEY-MATERIAL"

    def fake_run(command, *args, **kwargs):
        if command[-1] == "genkey":
            return _Completed(stdout=secret)
        return _Completed(stdout="PUBLICKEY==")

    monkeypatch.setattr(device.subprocess, "run", fake_run)

    with caplog.at_level("INFO", logger="LoomWG.wg"):
        result = keys.generate_keypair()

    assert result["private_key"] == secret
    assert all(secret not in record.getMessage() for record in caplog.records)


def test_run_command_redacts_sensitive_error_output(load_loomwg, monkeypatch, caplog):
    device = load_loomwg("wireguard")["wireguard"]
    secret = "SECRET-PRIVATE-KEY-MATERIAL"
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: _Completed(returncode=1, stderr=secret),
    )

    with (
        caplog.at_level("INFO", logger="LoomWG.wg"),
        pytest.raises(device.CommandError, match="output redacted"),
    ):
        device.run_command(["wg", "genkey"], sensitive=True)

    assert all(secret not in record.getMessage() for record in caplog.records)


def test_read_interface_redacts_key_material_from_logs(load_loomwg, monkeypatch, caplog):
    device = load_loomwg("wireguard")["wireguard"]
    private_key = "SECRET-INTERFACE-PRIVATE-KEY"
    psk = "SECRET-PEER-PRESHARED-KEY"
    dump = (
        f"{private_key} wServerPub== 51820 off\n"
        f"wPeerPub== {psk} 91.90.160.72:51361 "
        "10.66.66.4/32 0 1 2 off"
    )
    monkeypatch.setattr(
        device.subprocess, "run", lambda *a, **k: _Completed(stdout=dump)
    )

    with caplog.at_level("INFO", logger="LoomWG.wg"):
        device.read_interface("wg0")

    assert caplog.records, "expected at least one log record"
    assert all(private_key not in record.getMessage() for record in caplog.records)
    assert all(psk not in record.getMessage() for record in caplog.records)


def test_key_bearing_commands_are_marked_sensitive(load_loomwg, monkeypatch, tmp_path):
    modules = load_loomwg("wireguard", "validate", "client")
    device, validate, client = modules["wireguard"], modules["validate"], modules["client"]

    recorded: list = []

    def make_spy(output=""):
        def fake_run(command, timeout=15, input_text=None, *, sensitive=False):
            recorded.append((command, sensitive))
            return output

        return fake_run

    monkeypatch.setattr(device, "run_command", make_spy(_dump_output(int(time.time()))))
    device.read_interface("wg0")

    config_file = tmp_path / "wg0.conf"
    config_file.write_text("[Interface]\nAddress = 10.0.0.1/24\n", encoding="utf-8")
    monkeypatch.setattr(client, "config_path", lambda interface: config_file)
    monkeypatch.setattr(client, "parse_sections", lambda path: [])
    monkeypatch.setattr(client, "run_command", make_spy("header"))
    client._used_addresses("wg0")

    monkeypatch.setattr(validate, "run_command", make_spy(""))
    validate.validate_config_text("[Interface]\nPrivateKey = abc\n")
    monkeypatch.setattr(validate, "config_directory", lambda: tmp_path)
    validate.check_config({"interface": "wg0"})

    assert recorded, "expected recorded command calls"
    assert all(sensitive is True for _, sensitive in recorded)


# === lifecycle.py ===

def test_lifecycle_start_already_running(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: ["wg0"])
    called: list = []
    monkeypatch.setattr(lifecycle, "run_command", lambda *a, **k: called.append(a))

    assert lifecycle.start({"interface": "wg0"})["status"] == "already_running"
    assert called == []


def test_lifecycle_start_runs_wg_quick(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: [])
    commands: list = []
    monkeypatch.setattr(lifecycle, "run_command", lambda command, **k: commands.append(command))

    assert lifecycle.start({"interface": "wg0"})["status"] == "started"
    assert commands == [["wg-quick", "up", "wg0"]]


def test_lifecycle_stop_runs_wg_quick(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: ["wg0"])
    commands: list = []
    monkeypatch.setattr(lifecycle, "run_command", lambda command, **k: commands.append(command))

    assert lifecycle.stop({"interface": "wg0"})["status"] == "stopped"
    assert commands == [["wg-quick", "down", "wg0"]]


def test_lifecycle_stop_already_stopped(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: [])
    called: list = []
    monkeypatch.setattr(lifecycle, "run_command", lambda *a, **k: called.append(a))

    assert lifecycle.stop({"interface": "wg0"})["status"] == "already_stopped"
    assert called == []


def test_lifecycle_restart_down_then_up(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: ["wg0"])
    commands: list = []
    monkeypatch.setattr(lifecycle, "run_command", lambda command, **k: commands.append(command))

    assert lifecycle.restart({"interface": "wg0"})["status"] == "restarted"
    assert commands == [["wg-quick", "down", "wg0"], ["wg-quick", "up", "wg0"]]


def test_lifecycle_rejects_bad_interface(load_loomwg, monkeypatch):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]
    monkeypatch.setattr(lifecycle, "active_interfaces", lambda: [])

    with pytest.raises(ValueError):
        lifecycle.start({"interface": "bad iface!"})


def test_format_lifecycle(load_loomwg):
    lifecycle = load_loomwg("lifecycle")["lifecycle"]

    assert (
        lifecycle.format_lifecycle({"interface": "wg0", "status": "already_running"})
        == "wg0: already running"
    )
    assert lifecycle.format_lifecycle({"interface": "wg0", "status": "odd"}) == "wg0: odd"


# === peers/config.py: more cases ===

def test_config_path_uses_config_directory(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)

    assert config.config_path("wg0") == tmp_path / "wg0.conf"


def test_iter_peers_parses_blocks(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    key_a = _fake_key(11)
    key_b = _fake_key(12)
    (tmp_path / "wg0.conf").write_text(
        "[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\nPublicKey = {key_a}\n"
        "AllowedIPs = 10.0.0.2/32, fd00::2/128\nPresharedKey = psk\n\n"
        "[Peer]\nAllowedIPs = 10.0.0.3/32\n\n"
        f"[Peer]\nPublicKey = {key_b}\nAllowedIPs = 10.0.0.4/32\n",
        encoding="utf-8",
    )

    peers = config.iter_peers("wg0")

    assert [peer["public_key"] for peer in peers] == [key_a, key_b]
    assert peers[0]["allowed_ips"] == ["10.0.0.2/32", "fd00::2/128"]
    assert peers[0]["preshared_key"] == "psk"
    assert peers[1]["preshared_key"] is None


def test_iter_peers_missing_file(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)

    assert config.iter_peers("wg0") == []


def test_add_peer_missing_config(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)

    with pytest.raises(FileNotFoundError):
        config.add_peer("wg0", _fake_key(1), ["10.0.0.2/32"], None)


def test_upsert_peer_adds_then_detects_no_change(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    (tmp_path / "wg0.conf").write_text("[Interface]\nPrivateKey = x\n", encoding="utf-8")
    key = _fake_key(13)

    assert config.upsert_peer("wg0", key, ["10.0.0.2/32"], None) is True
    assert config.upsert_peer("wg0", key, ["10.0.0.2/32"], None) is False
    assert config.upsert_peer("wg0", key, ["10.0.0.9/32"], None) is True
    assert config.iter_peers("wg0")[0]["allowed_ips"] == ["10.0.0.9/32"]


def test_remove_peer_unknown_raises(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    (tmp_path / "wg0.conf").write_text("[Interface]\nPrivateKey = x\n", encoding="utf-8")

    with pytest.raises(ValueError):
        config.remove_peer("wg0", _fake_key(14))


def test_remove_peer_drops_only_target_block(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    keys = [_fake_key(seed) for seed in (20, 21, 22)]
    config_file = tmp_path / "wg0.conf"
    config_file.write_text(
        "[Interface]\nPrivateKey = x\n\n"
        + "\n\n".join(
            f"[Peer]\nPublicKey = {key}\nAllowedIPs = 10.0.0.{index}/32"
            for index, key in enumerate(keys, start=2)
        )
        + "\n",
        encoding="utf-8",
    )

    config.remove_peer("wg0", keys[1])

    text = config_file.read_text(encoding="utf-8")
    assert "[end]" not in text
    assert text.count("[Peer]") == 2
    assert keys[1] not in text
    assert keys[0] in text
    assert keys[2] in text


def test_remove_peer_sole_peer_keeps_valid_remainder(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(23)
    config_file = tmp_path / "wg0.conf"
    config_file.write_text(
        f"[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = {key}\nAllowedIPs = 10.0.0.2/32\n",
        encoding="utf-8",
    )

    config.remove_peer("wg0", key)

    text = config_file.read_text(encoding="utf-8")
    assert "[end]" not in text
    assert "[Peer]" not in text
    assert "[Interface]" in text
    assert config.iter_peers("wg0") == []


# === wg/validate.py: config text validation ===

def test_validate_config_text_cleans_temp_and_raises(load_loomwg, monkeypatch):
    validate = load_loomwg("validate")["validate"]
    seen: list[str] = []

    def fake_run_command(command, *args, **kwargs):
        seen.append(command[-1])
        raise validate.CommandError("wg-quick strip failed")

    monkeypatch.setattr(validate, "run_command", fake_run_command)

    with pytest.raises(validate.CommandError):
        validate.validate_config_text("[Interface]\nPrivateKey = x\n")

    assert len(seen) == 1
    assert seen[0].endswith(".conf")
    assert not Path(seen[0]).exists()


def test_validate_config_text_passes_and_cleans_temp(load_loomwg, monkeypatch):
    validate = load_loomwg("validate")["validate"]
    seen: list[str] = []

    def fake_run_command(command, *args, **kwargs):
        seen.append(command[-1])
        return "stripped"

    monkeypatch.setattr(validate, "run_command", fake_run_command)

    validate.validate_config_text("[Interface]\nPrivateKey = x\n")

    assert len(seen) == 1
    assert not Path(seen[0]).exists()


def test_write_rejects_invalid_config_keeps_destination(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    config_file = tmp_path / "wg0.conf"
    original = "[Interface]\nPrivateKey = x\n"
    config_file.write_text(original, encoding="utf-8")

    def reject(text: str) -> None:
        raise ValueError("invalid config")

    monkeypatch.setattr(config, "validate_config_text", reject)

    with pytest.raises(ValueError):
        config.add_peer("wg0", _fake_key(30), ["10.0.0.2/32"], None)
    assert config_file.read_text(encoding="utf-8") == original


def test_client_write_secret_rejects_invalid_config(load_loomwg, monkeypatch, tmp_path):
    client = load_loomwg("client")["client"]
    target = tmp_path / "peer.conf"

    def reject(text: str) -> None:
        raise ValueError("invalid config")

    monkeypatch.setattr(client, "validate_config_text", reject)

    with pytest.raises(ValueError):
        client._write_secret(target, "[Interface]\nPrivateKey = x\n")
    assert not target.exists()


# === clients/client.py: allocation + create_client ===

def test_allocate_addresses_exhaustion(load_loomwg, monkeypatch):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(
        client,
        "_used_addresses",
        lambda interface: {ip_address("10.66.66.1"), ip_address("10.66.66.2")},
    )

    with pytest.raises(ValueError, match="No free"):
        client._allocate_addresses("wg0", ["10.66.66.0/30"], [])


def test_client_addresses_rejects_wide_prefix(load_loomwg):
    client = load_loomwg("client")["client"]

    with pytest.raises(ValueError, match="/32 or /128"):
        client._client_addresses({"addresses": ["10.0.0.0/24"]}, "wg0", [], [])


def test_client_addresses_rejects_duplicates(load_loomwg):
    client = load_loomwg("client")["client"]

    with pytest.raises(ValueError, match="duplicates"):
        client._client_addresses(
            {"addresses": ["10.0.0.2/32", "10.0.0.2/32"]}, "wg0", [], []
        )


def test_client_addresses_rejects_non_list(load_loomwg):
    client = load_loomwg("client")["client"]

    with pytest.raises(ValueError):
        client._client_addresses({"addresses": "10.0.0.2/32"}, "wg0", [], [])


def test_create_client_success(load_loomwg, monkeypatch, tmp_path):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(
        client,
        "_server_settings",
        lambda interface: ("SERVERPUB", 51820, ["10.66.66.0/24"], ["10.66.66.1"]),
    )
    monkeypatch.setattr(
        client, "_client_addresses", lambda *args, **kwargs: ["10.66.66.2/32"]
    )
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())
    monkeypatch.setattr(
        client, "generate_keypair", lambda: {"private_key": "priv", "public_key": "pub"}
    )
    monkeypatch.setattr(client, "run_command", lambda *a, **k: "psk")
    added: list = []
    monkeypatch.setattr(client, "add_peer", lambda *a: added.append(a))
    written: list = []
    monkeypatch.setattr(client, "_write_secret", lambda path, content: written.append(path))
    monkeypatch.setattr(client, "_generate_qr", lambda content, path: written.append(path))
    conf = tmp_path / "alice.conf"
    qr = tmp_path / "alice.png"
    monkeypatch.setattr(client, "client_paths", lambda name: (conf, qr))

    result = client.create_client(
        {"interface": "wg0", "name": "alice", "endpoint": "1.2.3.4"}
    )

    assert result["public_key"] == "pub"
    assert result["addresses"] == ["10.66.66.2/32"]
    assert result["secret_persisted"] is True
    assert added == [("wg0", "pub", ["10.66.66.2/32"], "psk")]
    assert conf in written and qr in written


def test_create_client_rejects_existing_name(load_loomwg, monkeypatch, tmp_path):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(
        client,
        "_server_settings",
        lambda interface: ("SERVERPUB", 51820, ["10.66.66.0/24"], ["10.66.66.1"]),
    )
    monkeypatch.setattr(
        client, "_client_addresses", lambda *args, **kwargs: ["10.66.66.2/32"]
    )
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())
    monkeypatch.setattr(
        client, "generate_keypair", lambda: {"private_key": "priv", "public_key": "pub"}
    )
    monkeypatch.setattr(client, "run_command", lambda *a, **k: "psk")
    conf = tmp_path / "alice.conf"
    conf.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(client, "client_paths", lambda name: (conf, tmp_path / "alice.png"))

    with pytest.raises(ValueError, match="already exists"):
        client.create_client(
            {"interface": "wg0", "name": "alice", "endpoint": "1.2.3.4"}
        )
