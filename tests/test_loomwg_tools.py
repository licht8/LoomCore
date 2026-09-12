"""Unit tests for the expanded loomwg toolset."""

import base64

import pytest


def _key(seed: int) -> str:
    """Valid WireGuard key shape: base64 of 32 bytes."""
    return base64.b64encode(bytes([seed] * 32)).decode()


_VALID_CONFIG = "\n".join(
    [
        "[Interface]",
        "Address = 10.66.66.1/24, fd42:42:42::1/64",
        f"PrivateKey = {_key(1)}",
        "ListenPort = 51820",
        "",
        "[Peer]",
        f"PublicKey = {_key(2)}",
        "AllowedIPs = 10.66.66.2/32",
        "",
    ]
)


def test_schema_exposes_new_tools(load_loomwg):
    tools = load_loomwg("tools")["tools"]
    names = {tool["name"] for tool in tools.TOOLS_SCHEMA}
    expected = {
        "wg_show_config",
        "wg_check_config",
        "wg_stats",
        "wg_export_client",
        "wg_service_enable",
        "wg_service_disable",
    }
    assert expected <= names
    assert expected <= set(tools.HANDLERS)

    internal = next(t for t in tools.TOOLS_SCHEMA if t["name"] == "wg_get_client_artifact")
    assert internal.get("internal") is True


# === wg_show_config ===

def test_show_config_masks_secrets(load_loomwg, monkeypatch):
    view = load_loomwg("config_view")["config_view"]
    private = _key(1)
    live = f"[Interface]\nPrivateKey = {private}\nListenPort = 51820\n"
    monkeypatch.setattr(view, "run_command", lambda *args, **kwargs: live)

    result = view.show_config({"interface": "wg0"})

    text = result["content"][0]["text"]
    assert private not in text
    assert "(masked)" in text
    assert result["data"]["source"] == "live"
    assert result["data"]["config"]["interface"]["listen_port"] == "51820"

    shown = view.show_config({"interface": "wg0", "show_secrets": True})
    assert private in shown["content"][0]["text"]


# === wg_check_config ===

def test_check_config_valid(load_loomwg):
    check = load_loomwg("validate")["validate"]

    result = check.check_config({"content": _VALID_CONFIG})

    assert result["data"]["valid"] is True
    assert result["data"]["errors"] == []


def test_check_config_detects_problems(load_loomwg):
    check = load_loomwg("validate")["validate"]
    bad = "\n".join(
        [
            "[Interface]",
            "PrivateKey = not-a-key",
            "Address = 10.66.66.1/24",
            "",
            "[Peer]",
            f"PublicKey = {_key(2)}",
            "AllowedIPs = 10.0.0.0/8",
            "",
            "[Peer]",
            f"PublicKey = {_key(3)}",
            "AllowedIPs = 10.1.0.0/16",
            "",
            "[Peer]",
            f"PublicKey = {_key(3)}",
            "AllowedIPs = 192.168.0.0/16",
        ]
    )

    result = check.check_config({"content": bad})

    assert result["data"]["valid"] is False
    assert any("PrivateKey" in error for error in result["data"]["errors"])
    assert any("duplicate PublicKey" in error for error in result["data"]["errors"])
    assert any("overlap" in warning for warning in result["data"]["warnings"])


# === wg_export_client ===

def test_export_client_regenerates_qr(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "alice.conf").write_text(f"[Interface]\nPrivateKey = {_key(4)}\n", encoding="utf-8")
    calls = {}
    monkeypatch.setattr(client, "_generate_qr", lambda config, path: calls.setdefault("qr", path))
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: _key(5))

    result = client.export_client({"name": "alice"})

    assert result["config_token"] and result["qr_token"]
    assert result["rotated"] is False
    assert calls["qr"] == clients / "alice.png"


def test_export_client_missing_config_errors(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]

    with pytest.raises(ValueError):
        client.export_client({"name": "ghost"})


def test_export_client_rotate_recreates(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    monkeypatch.setattr(
        client, "create_client", lambda args: {"name": args["name"], "token": "t"}
    )

    result = client.export_client({"name": "ghost", "rotate": True})

    assert result["rotated"] is True


# === wg_service_enable / disable / autostart ===

def test_service_enable_disable(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    service = load_loomwg("service_mode")["service_mode"]
    monkeypatch.setattr(service, "active_interfaces", lambda: [])
    monkeypatch.setattr(service, "run_command", lambda *args, **kwargs: "")

    enabled = service.enable({"interface": "wg0"})
    assert enabled["data"]["autostart"] is True
    assert enabled["data"]["started"] is True
    assert (tmp_path / ".loomwg" / "autostart.json").is_file()

    disabled = service.disable({"interface": "wg0"})
    assert disabled["data"]["autostart"] is False
    assert not service._read_state().get("wg0")


def test_apply_autostart_brings_up_flagged(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    service = load_loomwg("service_mode")["service_mode"]
    service._write_state({"wg0": True})
    monkeypatch.setattr(service, "configured_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(service, "active_interfaces", lambda: [])
    calls = []
    monkeypatch.setattr(service, "run_command", lambda *args, **kwargs: calls.append(args) or "")

    service.apply_autostart()

    assert any(call[0][:2] == ["wg-quick", "up"] for call in calls)


# === wg_stats ===

def test_stats_snapshot(load_loomwg, monkeypatch):
    stats = load_loomwg("stats")["stats"]
    peers = [
        {
            "public_key": _key(6),
            "allowed_ips": ["10.0.0.2/32"],
            "receive_bytes": 100,
            "transmit_bytes": 300,
        },
        {
            "public_key": _key(7),
            "allowed_ips": ["10.0.0.3/32"],
            "receive_bytes": 50,
            "transmit_bytes": 50,
        },
    ]
    monkeypatch.setattr(stats, "read_interface", lambda interface: {"peers": peers, "listen_port": 51820})
    monkeypatch.setattr(stats, "has_link", lambda interface: True)
    monkeypatch.setattr(stats, "peer_is_online", lambda peer: False)

    result = stats.stats({"interface": "wg0", "top": 1})

    data = result["data"]
    assert data["peer_count"] == 2
    assert data["total_receive_bytes"] == 150
    assert data["total_transmit_bytes"] == 350
    assert len(data["peers"]) == 1
    assert data["peers"][0]["share"] == 80.0


# === wg_add_peer idempotency ===

def test_add_peer_idempotent_same_allowed_ips(load_loomwg, monkeypatch):
    tools = load_loomwg("tools")["tools"]
    key = _key(8)
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(
        tools,
        "read_interface",
        lambda interface: {"peers": [{"public_key": key, "allowed_ips": ["10.0.0.2/32"]}]},
    )
    ran = []
    monkeypatch.setattr(tools, "run_command", lambda *args, **kwargs: ran.append(args) or "")

    result = tools.add_peer({"public_key": key, "allowed_ips": ["10.0.0.2/32"]})

    assert result["data"]["changed"] is False
    assert ran == []


def test_add_peer_updates_existing_allowed_ips(load_loomwg, monkeypatch):
    tools = load_loomwg("tools")["tools"]
    key = _key(8)
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(
        tools,
        "read_interface",
        lambda interface: {"peers": [{"public_key": key, "allowed_ips": ["10.0.0.2/32"]}]},
    )
    calls = []
    monkeypatch.setattr(tools, "run_command", lambda *args, **kwargs: calls.append(args) or "")
    upserts = []
    monkeypatch.setattr(tools, "upsert_peer", lambda *args, **kwargs: upserts.append(args) or True)

    result = tools.add_peer({"public_key": key, "allowed_ips": ["10.0.0.9/32"]})

    assert result["data"]["changed"] is True
    assert calls and calls[0][0][:5] == ["wg", "set", "wg0", "peer", key]
    assert upserts
