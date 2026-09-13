"""Unit tests for LoomWG plugin logic without real WireGuard calls."""

import base64
import time
from datetime import UTC, datetime

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
    client = load_loomwg("client")["client"]
    assert client._safe_name("good-name_1") == "good-name_1"
    with pytest.raises(ValueError):
        client._safe_name("bad name")


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


# === install.py: LOOMWG_SKIP_FIREWALL ===

def _patch_setup_server(install, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "config_path", lambda interface: tmp_path / f"{interface}.conf")
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(
        install, "generate_keypair", lambda: {"private_key": "priv", "public_key": "pub"}
    )
    monkeypatch.setattr(
        install,
        "_detect_host",
        lambda: {"os": "rocky 10", "external_interface": "eth0", "firewall": "firewalld"},
    )
    monkeypatch.setattr(install, "_enable_forwarding", lambda: None)


def test_setup_server_skips_firewall(load_loomwg, monkeypatch, tmp_path):
    install = load_loomwg("install")["install"]
    _patch_setup_server(install, monkeypatch, tmp_path)
    monkeypatch.setenv("LOOMWG_SKIP_FIREWALL", "1")
    called = {"firewall": False}

    def fake_configure(*args, **kwargs):
        called["firewall"] = True
        return "firewalld"

    monkeypatch.setattr(install, "_configure_firewall", fake_configure)

    result = install.setup_server({"interface": "wgtest"})

    assert result["firewall"] == "skipped"
    assert called["firewall"] is False
    assert (tmp_path / "wgtest.conf").is_file()


def test_setup_server_configures_firewall_by_default(load_loomwg, monkeypatch, tmp_path):
    install = load_loomwg("install")["install"]
    _patch_setup_server(install, monkeypatch, tmp_path)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(install, "_configure_firewall", lambda *args, **kwargs: "firewalld")

    result = install.setup_server({"interface": "wgtest"})

    assert result["firewall"] == "firewalld"


# === install.py: dependency installation (in-container) ===

def test_dependency_report_all_present(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    report = install.dependency_report()

    assert report["ready"] is True
    assert report["missing"] == []
    assert report["package_manager"] == "apt"


def test_ensure_dependencies_noop_when_present(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    called = {"install": False}
    monkeypatch.setattr(
        install,
        "_container_shell",
        lambda *args, **kwargs: called.__setitem__("install", True),
    )

    result = install.ensure_dependencies()

    assert result["changed"] is False
    assert called["install"] is False


def test_ensure_dependencies_installs_missing(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    state = {"missing": "wg"}

    def fake_which(tool):
        return None if tool == state["missing"] else f"/usr/bin/{tool}"

    monkeypatch.setattr(install.shutil, "which", fake_which)
    calls: list[str] = []

    def fake_shell(command, timeout=300):
        calls.append(command)
        state["missing"] = None
        return ""

    monkeypatch.setattr(install, "_container_shell", fake_shell)

    result = install.ensure_dependencies()

    assert result["changed"] is True
    assert result["package_manager"] == "apt"
    assert result["missing_before"] == ["wg"]
    assert "wireguard-tools" in calls[0]
    assert result["missing_after"] == []


def test_ensure_dependencies_requires_package_manager(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    monkeypatch.setattr(install.shutil, "which", lambda tool: None)

    with pytest.raises(RuntimeError, match="no supported package manager"):
        install.ensure_dependencies()


def test_ensure_kernel_module_reports_host_hint(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(install, "_kernel_support_available", lambda: False)

    result = install.ensure_kernel_module()

    assert result["loaded"] is False
    assert "kmod-wireguard" in result["message"]


def test_ensure_kernel_module_available(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(install, "_kernel_support_available", lambda: True)

    result = install.ensure_kernel_module()

    assert result["loaded"] is True


def test_install_wireguard_returns_ready(load_loomwg, monkeypatch):
    install = load_loomwg("install")["install"]
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
