"""Unit tests for LoomWG plugin logic without real WireGuard calls."""

import base64
import io
import json
import subprocess
import sys
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


def test_reject_control_chars_allows_plain_values(load_loomwg):
    render = load_loomwg("render")["render"]
    assert render._reject_control_chars("1.2.3.4") == "1.2.3.4"
    for bad in ("a\nb", "a\rb", "a\tb", "a\x00b"):
        with pytest.raises(ValueError, match="control characters"):
            render._reject_control_chars(bad)


def test_build_client_config_rejects_injected_endpoint(load_loomwg):
    render = load_loomwg("render")["render"]
    with pytest.raises(ValueError, match="control characters"):
        render.build_client_config(
            "SERVERPUB",
            51820,
            ["10.66.66.2/32"],
            "priv",
            "psk",
            "1.2.3.4:51820\nPostUp = touch /tmp/pwned",
        )


@pytest.mark.parametrize(
    "field",
    ["addresses", "private_key", "preshared_key", "server_public_key", "dns"],
)
def test_build_client_config_rejects_injected_values(load_loomwg, field):
    render = load_loomwg("render")["render"]
    values = {
        "server_public_key": "SERVERPUB",
        "listen_port": 51820,
        "addresses": ["10.66.66.2/32"],
        "private_key": "priv",
        "preshared_key": "psk",
        "endpoint": "1.2.3.4",
        "dns": ["1.1.1.1"],
    }
    values[field] = ["10.66.66.2/32\nPostUp = x"] if field in {
        "addresses",
        "dns",
    } else "value\nPostUp = x"

    with pytest.raises(ValueError, match="control characters"):
        render.build_client_config(**values)


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

def _patch_setup_server(
    provision, monkeypatch, tmp_path, firewall="firewalld", iptables=True
):
    monkeypatch.setattr(provision, "config_path", lambda interface: tmp_path / f"{interface}.conf")
    monkeypatch.setattr(provision, "run_command", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(
        provision, "generate_keypair", lambda: {"private_key": "priv", "public_key": "pub"}
    )
    monkeypatch.setattr(
        provision,
        "detect_host",
        lambda: {
            "os": "rocky 10",
            "external_interface": "eth0",
            "firewall": firewall,
        },
    )
    monkeypatch.setattr(provision, "enable_forwarding", lambda: None)
    monkeypatch.setattr(
        provision.shutil,
        "which",
        lambda tool: f"/usr/sbin/{tool}" if iptables else None,
    )


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
    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "PostUp" not in content
    assert "iptables" not in content


def test_setup_server_configures_firewall_by_default(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "firewalld")

    result = provision.setup_server({"interface": "wgtest"})

    assert result["firewall"] == "firewalld"


def test_setup_server_scopes_masquerade_to_the_vpn_network(
    load_loomwg, monkeypatch, tmp_path
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall="iptables")
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "iptables")

    result = provision.setup_server({"interface": "wgtest"})

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "PostUp = " in content
    assert (
        "iptables -t nat -A POSTROUTING -s 10.66.66.0/24 -o eth0 -j MASQUERADE"
        in content
    )
    assert (
        "iptables -t nat -D POSTROUTING -s 10.66.66.0/24 -o eth0 -j MASQUERADE"
        in content
    )
    assert result["firewall"] == "iptables"


def test_setup_server_emits_nat_and_forward_hooks_for_ufw(
    load_loomwg, monkeypatch, tmp_path
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall="ufw")
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "ufw")

    result = provision.setup_server({"interface": "wgtest"})

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "iptables -A FORWARD -i %i -j ACCEPT" in content
    assert (
        "iptables -t nat -A POSTROUTING -s 10.66.66.0/24 -o eth0 -j MASQUERADE"
        in content
    )
    assert result["firewall"] == "ufw"


@pytest.mark.parametrize("firewall", ["firewalld", "nftables"])
def test_setup_server_leaves_nat_to_backends_that_own_it(
    load_loomwg, monkeypatch, tmp_path, firewall
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall=firewall)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: firewall)

    result = provision.setup_server({"interface": "wgtest"})

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "PostUp" not in content
    assert "iptables" not in content
    assert result["firewall"] == firewall


def test_setup_server_omits_iptables_hooks_on_an_nft_only_host(
    load_loomwg, monkeypatch, tmp_path
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(
        provision, monkeypatch, tmp_path, firewall="nftables", iptables=False
    )
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "nftables")

    result = provision.setup_server({"interface": "wgtest"})

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "PostUp" not in content
    assert "PostDown" not in content
    assert "iptables" not in content
    assert result["firewall"] == "nftables"


def test_setup_server_omits_hooks_for_a_hooked_backend_without_iptables(
    load_loomwg, monkeypatch, tmp_path
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall="ufw", iptables=False)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *args, **kwargs: "ufw")

    result = provision.setup_server({"interface": "wgtest"})

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "PostUp" not in content
    assert "PostDown" not in content
    assert "iptables" not in content
    assert result["firewall"] == "ufw"


def test_setup_server_rejects_an_unsupported_firewall(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall="none")
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)

    with pytest.raises(RuntimeError, match="No supported firewall"):
        provision.setup_server({"interface": "wgtest"})

    assert not (tmp_path / "wgtest.conf").exists()


def test_setup_server_rolls_back_config_when_firewall_fails(
    load_loomwg, monkeypatch, tmp_path
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path, firewall="iptables")
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)

    def boom(*args, **kwargs):
        raise RuntimeError("firewall exploded")

    monkeypatch.setattr(provision, "configure_firewall", boom)

    with pytest.raises(RuntimeError, match="firewall exploded"):
        provision.setup_server({"interface": "wgtest"})

    # The config must not survive a failed provision, or every retry would
    # abort with "already exists".
    assert not (tmp_path / "wgtest.conf").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"ipv4_network": "10.0.0.1/32"},
        {"ipv6_network": "fd42:42:42::1/128"},
    ],
)
def test_setup_server_rejects_a_prefix_too_small(
    load_loomwg, monkeypatch, tmp_path, arguments
):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="prefix is too small"):
        provision.setup_server({"interface": "wgtest", **arguments})

    assert not (tmp_path / "wgtest.conf").exists()


def test_setup_server_accepts_point_to_point_prefixes(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *a, **k: "firewalld")

    result = provision.setup_server(
        {"interface": "wgtest", "ipv4_network": "10.0.0.0/31"}
    )

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "Address = 10.0.0.1/31" in content
    assert result["ipv4_network"] == "10.0.0.0/31"


def test_setup_server_accepts_dns_search_domains(load_loomwg, monkeypatch, tmp_path):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)
    monkeypatch.delenv("LOOMWG_SKIP_FIREWALL", raising=False)
    monkeypatch.setattr(provision, "configure_firewall", lambda *a, **k: "firewalld")

    provision.setup_server(
        {"interface": "wgtest", "dns": ["1.1.1.1", "corp.example.com"]}
    )

    content = (tmp_path / "wgtest.conf").read_text(encoding="utf-8")
    assert "DNS = 1.1.1.1, corp.example.com" in content


@pytest.mark.parametrize(
    "payload",
    [
        "1.1.1.1\nPostUp = evil",
        "1.1.1.1\u0085PostUp = evil",
        "1.1.1.1\u2028PostUp = evil",
        "1.1.1.1\u2029PostUp = evil",
    ],
)
def test_setup_server_rejects_injected_dns(load_loomwg, monkeypatch, tmp_path, payload):
    provision = load_loomwg("provision")["provision"]
    _patch_setup_server(provision, monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="control characters"):
        provision.setup_server({"interface": "wgtest", "dns": [payload]})

    assert not (tmp_path / "wgtest.conf").exists()


def test_configure_firewall_iptables_leaves_nat_to_the_hooks(load_loomwg, monkeypatch):
    provision = load_loomwg("provision")["provision"]
    commands: list[str] = []
    monkeypatch.setattr(provision, "run_shell", lambda command: commands.append(command))

    provision.configure_firewall("iptables", "wg0", "eth0", 51820)

    assert len(commands) == 1
    assert "--dport 51820 -j ACCEPT" in commands[0]
    assert "MASQUERADE" not in commands[0]


def test_configure_firewall_firewalld_adds_forward_accept_rules(load_loomwg, monkeypatch):
    provision = load_loomwg("provision")["provision"]
    commands: list[str] = []
    monkeypatch.setattr(provision, "run_shell", lambda command: commands.append(command))

    provision.configure_firewall("firewalld", "wg0", "eth0", 51820)

    assert len(commands) == 1
    assert "--direct --add-rule ipv4 filter FORWARD 0 -i wg0 -j ACCEPT" in commands[0]
    assert "--direct --add-rule ipv4 filter FORWARD 0 -o wg0 -j ACCEPT" in commands[0]
    assert "--direct --add-rule ipv6 filter FORWARD 0 -i wg0 -j ACCEPT" in commands[0]
    assert "--direct --add-rule ipv6 filter FORWARD 0 -o wg0 -j ACCEPT" in commands[0]
    assert "|| true" in commands[0]
    assert commands[0].endswith("firewall-cmd --reload")


def test_configure_firewall_nftables_leaves_forwarding_to_host_policy(
    load_loomwg, monkeypatch
):
    provision = load_loomwg("provision")["provision"]
    commands: list[str] = []
    monkeypatch.setattr(provision, "run_shell", lambda command: commands.append(command))

    provision.configure_firewall("nftables", "wg0", "eth0", 51820)

    assert len(commands) == 1
    assert "postrouting" in commands[0]
    assert "forward" not in commands[0]


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
    assert "wireguard-dkms" in result["message"]
    assert "kmod-wireguard" in result["message"]


def test_ensure_kernel_module_available(load_loomwg, monkeypatch):
    install = load_loomwg("dependencies")["dependencies"]
    monkeypatch.setattr(install, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(install, "_kernel_support_available", lambda: True)

    result = install.ensure_kernel_module()

    assert result["loaded"] is True


def test_kernel_probe_removes_the_link_on_every_path(load_loomwg, monkeypatch):
    dependencies = load_loomwg("dependencies")["dependencies"]
    calls: list[list[str]] = []
    monkeypatch.setattr(
        dependencies, "run_command", lambda command, **kwargs: calls.append(command)
    )

    assert dependencies._kernel_support_available() is True
    interface = dependencies._PROBE_INTERFACE
    assert calls == [
        ["ip", "link", "delete", "dev", interface],
        ["ip", "link", "add", "dev", interface, "type", "wireguard"],
        ["ip", "link", "delete", "dev", interface],
    ]


def test_kernel_probe_tears_down_after_a_failed_probe(load_loomwg, monkeypatch):
    dependencies = load_loomwg("dependencies")["dependencies"]
    deletes: list[list[str]] = []

    def fake_run(command, **kwargs):
        if "add" in command:
            raise dependencies.CommandError("RTNETLINK answers: Operation not supported")
        deletes.append(command)
        return ""

    monkeypatch.setattr(dependencies, "run_command", fake_run)

    assert dependencies._kernel_support_available() is False
    assert len(deletes) == 2


def test_kernel_probe_recovers_from_a_leftover_link(load_loomwg, monkeypatch):
    dependencies = load_loomwg("dependencies")["dependencies"]
    interface = dependencies._PROBE_INTERFACE
    links = {interface}

    def fake_run(command, **kwargs):
        if command[1:3] == ["link", "delete"]:
            if interface not in links:
                raise dependencies.CommandError("Cannot find device")
            links.discard(interface)
            return ""
        if interface in links:
            raise dependencies.CommandError("RTNETLINK answers: File exists")
        links.add(interface)
        return ""

    monkeypatch.setattr(dependencies, "run_command", fake_run)

    assert dependencies._kernel_support_available() is True
    assert links == set()


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


def test_remove_peer_ignores_matching_key_in_interface_block(
    load_loomwg, monkeypatch, tmp_path
):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(24)
    config_file = tmp_path / "wg0.conf"
    original = f"[Interface]\nPrivateKey = x\nPublicKey = {key}\n"
    config_file.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        config.remove_peer("wg0", key)

    assert config_file.read_text(encoding="utf-8") == original


def test_add_peer_ignores_commented_public_key(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(25)
    (tmp_path / "wg0.conf").write_text(
        f"[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\n# PublicKey = {key}\nAllowedIPs = 10.0.0.2/32\n",
        encoding="utf-8",
    )

    config.add_peer("wg0", key, ["10.0.0.3/32"], None)

    assert config.iter_peers("wg0") == [
        {"public_key": key, "allowed_ips": ["10.0.0.3/32"], "preshared_key": None}
    ]


def test_remove_peer_accepts_spacing_variant(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(26)
    (tmp_path / "wg0.conf").write_text(
        f"[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\nPublicKey={key}\nAllowedIPs = 10.0.0.2/32\n",
        encoding="utf-8",
    )

    config.remove_peer("wg0", key)

    assert config.iter_peers("wg0") == []


def test_upsert_peer_accepts_spacing_variant(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(27)
    config_file = tmp_path / "wg0.conf"
    config_file.write_text(
        f"[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\nPublicKey={key}\nAllowedIPs = 10.0.0.2/32\n",
        encoding="utf-8",
    )

    assert config.upsert_peer("wg0", key, ["10.0.0.9/32"], None) is True

    text = config_file.read_text(encoding="utf-8")
    assert text.count("[Peer]") == 1
    assert config.iter_peers("wg0")[0]["allowed_ips"] == ["10.0.0.9/32"]


def test_iter_peers_matches_non_canonical_key_case(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(28)
    psk = _fake_key(29)
    (tmp_path / "wg0.conf").write_text(
        f"[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\npublickey = {key}\nallowedips = 10.0.0.2/32\npresharedkey = {psk}\n",
        encoding="utf-8",
    )

    assert config.iter_peers("wg0") == [
        {"public_key": key, "allowed_ips": ["10.0.0.2/32"], "preshared_key": psk}
    ]

    config.remove_peer("wg0", key)

    assert config.iter_peers("wg0") == []


def test_remove_peer_rejects_invalid_key(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    config_file = tmp_path / "wg0.conf"
    original = "[Interface]\nPrivateKey = x\n"
    config_file.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="public key"):
        config.remove_peer("wg0", "")

    assert config_file.read_text(encoding="utf-8") == original


def test_upsert_peer_treats_reordered_allowed_ips_as_no_change(
    load_loomwg, monkeypatch, tmp_path
):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key = _fake_key(33)
    (tmp_path / "wg0.conf").write_text(
        f"[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = {key}\n"
        "AllowedIPs = 10.0.0.2/32, 10.0.0.3/32\n",
        encoding="utf-8",
    )

    assert config.upsert_peer("wg0", key, ["10.0.0.3/32", "10.0.0.2/32"], None) is False


def test_upsert_peer_does_not_grow_separators(load_loomwg, monkeypatch, tmp_path):
    config = load_loomwg("config")["config"]
    monkeypatch.setattr(config, "config_directory", lambda: tmp_path)
    monkeypatch.setattr(config, "validate_config_text", lambda text: None)
    key_a = _fake_key(31)
    key_b = _fake_key(32)
    config_file = tmp_path / "wg0.conf"
    config_file.write_text(
        "[Interface]\nPrivateKey = x\n\n"
        f"[Peer]\nPublicKey = {key_a}\nAllowedIPs = 10.0.0.2/32\n\n"
        f"[Peer]\nPublicKey = {key_b}\nAllowedIPs = 10.0.0.3/32\n",
        encoding="utf-8",
    )

    for suffix in ("4", "5", "6"):
        config.upsert_peer("wg0", key_a, [f"10.0.0.{suffix}/32"], None)

    text = config_file.read_text(encoding="utf-8")
    assert "\n\n\n" not in text
    assert text.count("[Peer]") == 2


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


def test_check_config_reports_unreadable_interface_file(load_loomwg, monkeypatch, tmp_path):
    validate = load_loomwg("validate")["validate"]
    (tmp_path / "wg0.conf").write_bytes(b"\xff\xfe\x00")
    monkeypatch.setattr(validate, "config_directory", lambda: tmp_path)

    result = validate.check_config({"interface": "wg0"})

    assert result["data"]["valid"] is False
    assert result["data"]["errors"][0].startswith("cannot read")
    assert "INVALID" in result["content"][0]["text"]


def test_check_config_reports_unreadable_explicit_path(load_loomwg, tmp_path):
    validate = load_loomwg("validate")["validate"]
    target = tmp_path / "broken.conf"
    target.write_bytes(b"\xff\xfe\x00")

    result = validate.check_config({"path": str(target)})

    assert result["data"]["valid"] is False
    assert result["data"]["errors"][0].startswith("cannot read")
    assert "INVALID" in result["content"][0]["text"]


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


def test_client_addresses_rejects_explicit_empty(load_loomwg, monkeypatch):
    client = load_loomwg("client")["client"]
    allocation_calls = []
    monkeypatch.setattr(
        client,
        "_allocate_addresses",
        lambda *args, **kwargs: allocation_calls.append(args) or ["10.0.0.2/32"],
    )

    for empty in ({"addresses": []}, {"addresses": ""}, {"allowed_ips": []}):
        with pytest.raises(ValueError, match="non-empty"):
            client._client_addresses(empty, "wg0", [], [])

    assert allocation_calls == []


def test_allocate_addresses_ipv6_near_the_top_does_not_overflow(load_loomwg, monkeypatch):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())

    result = client._allocate_addresses(
        "wg0",
        ["10.0.0.0/30", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffc/126"],
        ["10.0.0.1"],
    )

    assert result == [
        "10.0.0.2/32",
        "ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffe/128",
    ]


def test_allocate_addresses_keeps_ipv6_version_for_a_small_base(load_loomwg, monkeypatch):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())

    result = client._allocate_addresses("wg0", ["10.0.0.0/24", "::/64"], ["10.0.0.1"])

    assert result == ["10.0.0.2/32", "::2/128"]


def test_allocate_addresses_ipv6_overflow_is_reported_as_exhaustion(
    load_loomwg, monkeypatch
):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(client, "_used_addresses", lambda interface: set())

    with pytest.raises(ValueError, match="No free"):
        client._allocate_addresses(
            "wg0",
            ["10.0.0.0/30", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffe/128"],
            [],
        )


def test_remove_client_artifacts_survives_a_bad_private_key(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    conf_dir = tmp_path / "clients" / "conf"
    conf_dir.mkdir(parents=True)
    (conf_dir / "stale.conf").write_text(
        "[Interface]\nPrivateKey = bad\n", encoding="utf-8"
    )
    (conf_dir / "target.conf").write_text(
        "[Interface]\nPrivateKey = good\n", encoding="utf-8"
    )

    def fake_run(argv, **kwargs):
        if kwargs.get("input_text") == "bad":
            raise client.CommandError("wg pubkey failed")
        return "PUBTARGET"

    monkeypatch.setattr(client, "run_command", fake_run)

    removed = client.remove_client_artifacts("PUBTARGET")

    assert removed == ["target"]
    assert not (conf_dir / "target.conf").exists()
    assert (conf_dir / "stale.conf").exists()


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


def test_rebuild_client_rejects_injected_endpoint(load_loomwg, monkeypatch, tmp_path):
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(
        client,
        "_server_settings",
        lambda interface: ("SERVERPUB", 51820, ["10.66.66.0/24"], ["10.66.66.1"]),
    )
    conf = tmp_path / "alice.conf"
    qr = tmp_path / "alice.png"
    monkeypatch.setattr(client, "client_paths", lambda name: (conf, qr))
    record = {
        "private_key": "priv",
        "preshared_key": "psk",
        "addresses": ["10.66.66.2/32"],
        "endpoint": "1.2.3.4:51820\nPostUp = touch /tmp/pwned",
    }

    with pytest.raises(ValueError, match="control characters"):
        client.rebuild_client("alice", record, "wg0")

    assert not conf.exists() and not qr.exists()


# === clients/qr.py: qrencode failures and file permissions ===

def test_generate_qr_secures_the_file_before_writing(load_loomwg, monkeypatch, tmp_path):
    qr = load_loomwg("qr")["qr"]
    qr_path = tmp_path / "alice.png"
    calls: list = []
    original_open = qr.os.open

    def spy_open(path, flags, mode=0o777):
        calls.append(("open", flags, mode))
        return original_open(path, flags, mode)

    def fake_run(*args, **kwargs):
        calls.append(("run", qr_path.exists()))
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(qr.os, "open", spy_open)
    monkeypatch.setattr(qr.subprocess, "run", fake_run)

    qr.generate_qr("PrivateKey = secret\n", qr_path)

    assert calls == [
        ("open", qr.os.O_CREAT | qr.os.O_WRONLY, 0o600),
        ("run", True),
    ]


def test_generate_qr_normalizes_timeout(load_loomwg, monkeypatch, tmp_path):
    qr = load_loomwg("qr")["qr"]

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(qr.subprocess, "run", timeout)

    with pytest.raises(qr.CommandError, match="qrencode failed"):
        qr.generate_qr("PrivateKey = secret\n", tmp_path / "alice.png")


def test_generate_qr_reports_missing_qrencode(load_loomwg, monkeypatch, tmp_path):
    qr = load_loomwg("qr")["qr"]

    def missing(*args, **kwargs):
        raise FileNotFoundError("qrencode")

    monkeypatch.setattr(qr.subprocess, "run", missing)

    with pytest.raises(qr.CommandError, match="not available"):
        qr.generate_qr("PrivateKey = secret\n", tmp_path / "alice.png")


def test_generate_qr_reports_unwritable_target(load_loomwg, monkeypatch, tmp_path):
    qr = load_loomwg("qr")["qr"]

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(qr.os, "open", denied)

    with pytest.raises(qr.CommandError, match="Cannot create QR code file"):
        qr.generate_qr("PrivateKey = secret\n", tmp_path / "alice.png")


def test_generate_qr_retightens_the_mode_after_writing(load_loomwg, monkeypatch, tmp_path):
    qr = load_loomwg("qr")["qr"]
    qr_path = tmp_path / "alice.png"
    events: list = []
    original_chmod = qr.os.chmod

    def spy_chmod(path, mode):
        events.append(("chmod", mode))
        return original_chmod(path, mode)

    def fake_run(*args, **kwargs):
        events.append(("run", qr_path.is_file()))
        qr_path.write_bytes(b"PNG")
        return _Completed()

    monkeypatch.setattr(qr.os, "chmod", spy_chmod)
    monkeypatch.setattr(qr.subprocess, "run", fake_run)

    qr.generate_qr("PrivateKey = secret\n", qr_path)

    assert events == [
        ("chmod", 0o600),
        ("run", True),
        ("chmod", 0o600),
    ]


def test_generate_qr_discards_the_placeholder_on_failure(
    load_loomwg, monkeypatch, tmp_path
):
    qr = load_loomwg("qr")["qr"]
    qr_path = tmp_path / "alice.png"

    def failing(*args, **kwargs):
        return _Completed(returncode=1, stderr="boom")

    monkeypatch.setattr(qr.subprocess, "run", failing)
    with pytest.raises(qr.CommandError, match="boom"):
        qr.generate_qr("PrivateKey = secret\n", qr_path)
    assert not qr_path.exists()

    def missing(*args, **kwargs):
        raise FileNotFoundError("qrencode")

    monkeypatch.setattr(qr.subprocess, "run", missing)
    with pytest.raises(qr.CommandError, match="not available"):
        qr.generate_qr("PrivateKey = secret\n", qr_path)
    assert not qr_path.exists()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(qr.subprocess, "run", timeout)
    with pytest.raises(qr.CommandError, match="qrencode failed"):
        qr.generate_qr("PrivateKey = secret\n", qr_path)
    assert not qr_path.exists()


def test_generate_qr_keeps_an_existing_artifact_on_early_failure(
    load_loomwg, monkeypatch, tmp_path
):
    qr = load_loomwg("qr")["qr"]
    qr_path = tmp_path / "alice.png"
    qr_path.write_bytes(b"OLD-PNG")

    def missing(*args, **kwargs):
        raise FileNotFoundError("qrencode")

    monkeypatch.setattr(qr.subprocess, "run", missing)

    with pytest.raises(qr.CommandError, match="not available"):
        qr.generate_qr("PrivateKey = secret\n", qr_path)

    assert qr_path.read_bytes() == b"OLD-PNG"


def test_generate_qr_discards_an_existing_artifact_once_writing_starts(
    load_loomwg, monkeypatch, tmp_path
):
    qr = load_loomwg("qr")["qr"]
    qr_path = tmp_path / "alice.png"
    qr_path.write_bytes(b"OLD-PNG")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(qr.subprocess, "run", timeout)

    with pytest.raises(qr.CommandError, match="qrencode failed"):
        qr.generate_qr("PrivateKey = secret\n", qr_path)

    assert not qr_path.exists()


# === protocol.py: transport resilience ===

def _run_protocol(protocol, monkeypatch, handlers, *lines: str) -> list[dict]:
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    protocol.run(handlers, [])
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_protocol_answers_unexpected_tool_exception(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]

    def boom(arguments):
        raise TypeError("boom")

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


def test_protocol_answers_unserializable_result(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
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


def test_protocol_non_object_payload_is_invalid_request(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
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
    assert [r["id"] for r in responses[:4]] == [None] * 4
    assert responses[4]["id"] == 9
    assert responses[4]["result"] == {"tools": []}


def _deep_structure(depth: int = 10000) -> dict:
    value: dict = {}
    for _ in range(depth):
        value = {"a": value}
    return value


def test_protocol_deeply_nested_line_is_parse_error(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        "[" * 10000 + "]" * 10000,
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert responses[1]["result"] == {"tools": []}


def test_protocol_deeply_nested_result_is_answered(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
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


def test_protocol_keeps_tool_errors_as_32000(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]

    def boom(arguments):
        raise RuntimeError("nope")

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
    )

    assert responses[0]["error"]["code"] == -32000
    assert "nope" in responses[0]["error"]["message"]


def test_protocol_parse_error_and_unknown_method(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        "not json",
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"}),
    )

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert responses[1]["error"]["code"] == -32601
    assert responses[1]["id"] == 7


def test_protocol_serialize_falls_back_to_constant_payload(load_loomwg):
    protocol = load_loomwg("protocol")["protocol"]
    payload = {"jsonrpc": "2.0", "id": _deep_structure(10000), "result": None}

    response = json.loads(protocol._serialize(payload))

    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal error"
    assert response["id"] is None


def test_protocol_non_object_params_is_invalid_params(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": ["x"],
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}),
    )

    assert responses[0]["id"] == 5
    assert responses[0]["error"] == {"code": -32602, "message": "Invalid params"}
    assert responses[1]["result"] == {"tools": []}


def test_protocol_non_object_arguments_is_invalid_arguments(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]
    responses = _run_protocol(
        protocol,
        monkeypatch,
        {"boom": lambda arguments: {}},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": "boom"},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}),
    )

    assert responses[0]["id"] == 5
    assert responses[0]["error"] == {"code": -32602, "message": "Invalid arguments"}
    assert responses[1]["result"] == {"tools": []}


def test_protocol_id_less_request_error_uses_null_id(load_loomwg, monkeypatch):
    protocol = load_loomwg("protocol")["protocol"]

    def boom(arguments):
        raise TypeError("boom")

    responses = _run_protocol(
        protocol,
        monkeypatch,
        {"boom": boom},
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        ),
    )

    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32603


# === shell.py: timeout handling ===

def test_run_shell_reports_a_timeout_as_runtime_error(load_loomwg, monkeypatch):
    shell = load_loomwg("shell")["shell"]

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0], timeout=kwargs["timeout"], output="partial", stderr="stuck"
        )

    monkeypatch.setattr(shell.subprocess, "run", timeout)

    with pytest.raises(RuntimeError, match="timed out after 300s: stuck"):
        shell.run_shell("wg-quick up wg0")


# === util/files.py: atomic writes ===

def test_atomic_write_fsyncs_the_temp_file_before_replace(load_loomwg, monkeypatch, tmp_path):
    files = load_loomwg("files")["files"]
    events: list[str] = []
    real_fsync = files.os.fsync
    real_replace = files.os.replace

    def spy_fsync(descriptor, *args, **kwargs):
        events.append("fsync")
        return real_fsync(descriptor, *args, **kwargs)

    def spy_replace(source, destination, *args, **kwargs):
        events.append("replace")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(files.os, "fsync", spy_fsync)
    monkeypatch.setattr(files.os, "replace", spy_replace)

    target = tmp_path / "wg0.conf"
    files.atomic_write_text(target, "[Interface]\n", mode=0o600)

    assert target.read_text(encoding="utf-8") == "[Interface]\n"
    assert events.index("fsync") < events.index("replace")


def test_atomic_write_replaces_content_and_cleans_up(load_loomwg, tmp_path):
    files = load_loomwg("files")["files"]
    target = tmp_path / "wg0.conf"
    target.write_text("old", encoding="utf-8")

    files.atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(".wg0.conf.*")) == []
