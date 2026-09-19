"""Unit tests for the expanded LoomWG toolset and the JSON client store."""

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
        "wg_show_interface_config",
        "wg_check_interface_config",
        "wg_stats",
        "wg_install_dependencies",
        "wg_export_existing_clients",
        "wg_service_enable",
        "wg_service_disable",
    }
    assert expected <= names
    assert "wg_create_client" not in names
    assert "wg_export_client" not in names
    assert "wg_backfill_clients" not in names
    assert expected <= set(tools.HANDLERS)

    internal = next(t for t in tools.TOOLS_SCHEMA if t["name"] == "wg_get_client_artifact")
    assert internal.get("internal") is True


def test_every_tool_declares_an_explicit_risk(load_loomwg):
    schemas = load_loomwg("schemas")["schemas"]
    assert {tool["name"] for tool in schemas.TOOLS_SCHEMA} == set(schemas._TOOL_RISK)
    assert {tool["risk"] for tool in schemas.TOOLS_SCHEMA} <= {
        "safe",
        "moderate",
        "dangerous",
    }


def test_risk_declaration_fails_closed(load_loomwg):
    schemas = load_loomwg("schemas")["schemas"]
    with pytest.raises(RuntimeError, match=r"missing=\['ghost'\]"):
        schemas._declare_risk([{"name": "ghost"}], {})
    with pytest.raises(RuntimeError, match=r"unknown=\['stale'\]"):
        schemas._declare_risk(
            [{"name": "present"}], {"present": "safe", "stale": "safe"}
        )


# === clients_store ===

def test_store_roundtrip(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]

    store.upsert("alice", {"public_key": _key(1), "addresses": ["10.0.0.2/32"]})

    assert store.get("alice")["public_key"] == _key(1)
    assert store.find_by_public_key(_key(1)) == "alice"
    assert store.remove("alice") is True
    assert store.load() == {}


def test_load_treats_a_missing_store_as_empty(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]

    assert store.store_path().is_file() is False
    assert store.load() == {}


def test_load_rejects_a_corrupt_store(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable or corrupt"):
        store.load()


def test_load_rejects_a_non_object_document(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="expected a JSON object"):
        store.load()


def test_load_rejects_non_object_records(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"alice": "oops", "bob": {"public_key": "x"}}', encoding="utf-8")

    with pytest.raises(ValueError, match="non-object records for alice"):
        store.load()
    with pytest.raises(ValueError, match="non-object records for alice"):
        store.upsert("carol", {"public_key": _key(1)})

    assert "alice" in path.read_text(encoding="utf-8")


def test_upsert_does_not_overwrite_a_corrupt_store(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unreadable or corrupt"):
        store.upsert("alice", {"public_key": _key(1)})

    assert path.read_text(encoding="utf-8") == "{not json"


def test_resolve_name_matches_variants(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    key = _key(2)
    store.upsert(f"peer-{key[:12]}", {"public_key": key, "addresses": ["10.0.0.2/32"]})

    assert store.resolve_name(f"peer-{key[:12]}") == f"peer-{key[:12]}"
    assert store.resolve_name(f"peer-peer-{key[:12]}") == f"peer-{key[:12]}"
    assert store.resolve_name(key[:12]) == f"peer-{key[:12]}"


def test_resolve_name_errors_list_available(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    store.upsert("alice", {"public_key": _key(1), "addresses": []})

    with pytest.raises(ValueError, match="alice"):
        store.resolve_name("bob")


def test_resolve_name_rejects_ambiguous_prefix(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    store.upsert("one", {"public_key": "AAAABBBB", "addresses": []})
    store.upsert("two", {"public_key": "AAAACCCC", "addresses": []})

    with pytest.raises(ValueError, match="ambiguous"):
        store.resolve_name("AAAA")


# === wg_show_interface_config ===

def test_show_config_masks_secrets(load_loomwg, monkeypatch):
    view = load_loomwg("config_view")["config_view"]
    private = _key(1)
    live = f"[Interface]\nPrivateKey = {private}\nListenPort = 51820\n"
    monkeypatch.setattr(view, "run_command", lambda *args, **kwargs: live)

    result = view.show_config({"interface": "wg0"})

    text = result["content"][0]["text"]
    assert private not in text
    assert "(masked)" in text
    assert result["data"]["config"]["interface"]["listen_port"] == "51820"

    # show_secrets was removed from the tool schema; even if a caller passes it
    # the server configuration must stay masked (defense in depth).
    shown = view.show_config({"interface": "wg0", "show_secrets": True})
    assert private not in shown["content"][0]["text"]
    assert "(masked)" in shown["content"][0]["text"]


# === wg_check_interface_config ===

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


def test_check_config_flags_unknown_section(load_loomwg):
    check = load_loomwg("validate")["validate"]
    text = "\n".join(
        [
            "[Interface]",
            f"PrivateKey = {_key(7)}",
            "Address = 10.66.66.1/24",
            "",
            "[end]",
        ]
    )
    result = check.check_config({"content": text})
    assert result["data"]["valid"] is False
    assert any("[end]" in error for error in result["data"]["errors"])


# === wg_create_peer ===

def test_add_peer_idempotent_same_allowed_ips(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
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

    result = tools.add_peer({"name": "peer1", "public_key": key, "allowed_ips": ["10.0.0.2/32"]})

    assert result["data"]["changed"] is False
    assert ran == []


def test_add_peer_updates_existing_allowed_ips(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
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

    result = tools.add_peer({"name": "peer1", "public_key": key, "allowed_ips": ["10.0.0.9/32"]})

    assert result["data"]["changed"] is True
    assert calls and calls[0][0][:5] == ["wg", "set", "wg0", "peer", key]
    assert upserts


def test_add_peer_restores_previous_allowed_ips_when_persist_fails(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
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

    def fail(*args, **kwargs):
        raise OSError("config write failed")

    monkeypatch.setattr(tools, "upsert_peer", fail)

    with pytest.raises(OSError, match="config write failed"):
        tools.add_peer({"name": "peer1", "public_key": key, "allowed_ips": ["10.0.0.9/32"]})

    assert calls[0][0][5:] == ["allowed-ips", "10.0.0.9/32"]
    assert calls[1][0][5:] == ["allowed-ips", "10.0.0.2/32"]


def test_add_peer_restores_previous_preshared_key_when_persist_fails(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    tools = load_loomwg("tools")["tools"]
    key = _key(9)
    old_psk = _key(10)
    new_psk = _key(11)
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(
        tools,
        "read_interface",
        lambda interface: {"peers": [{"public_key": key, "allowed_ips": ["10.0.0.2/32"]}]},
    )
    monkeypatch.setattr(
        tools,
        "iter_peers",
        lambda interface: [
            {"public_key": key, "allowed_ips": ["10.0.0.2/32"], "preshared_key": old_psk}
        ],
    )
    calls = []
    monkeypatch.setattr(
        tools, "run_command", lambda *args, **kwargs: calls.append((args, kwargs)) or ""
    )

    def fail(*args, **kwargs):
        raise OSError("config write failed")

    monkeypatch.setattr(tools, "upsert_peer", fail)

    with pytest.raises(OSError, match="config write failed"):
        tools.add_peer(
            {
                "name": "peer1",
                "public_key": key,
                "allowed_ips": ["10.0.0.9/32"],
                "preshared_key": new_psk,
            }
        )

    assert calls[0][0][0][5:] == ["allowed-ips", "10.0.0.9/32", "preshared-key", "/dev/stdin"]
    assert calls[0][1]["input_text"] == new_psk
    assert calls[1][0][0][5:] == ["allowed-ips", "10.0.0.2/32", "preshared-key", "/dev/stdin"]
    assert calls[1][1]["input_text"] == old_psk


def test_add_peer_leaves_live_key_when_previous_lookup_fails(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    tools = load_loomwg("tools")["tools"]
    key = _key(12)
    new_psk = _key(13)
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(
        tools,
        "read_interface",
        lambda interface: {"peers": [{"public_key": key, "allowed_ips": ["10.0.0.2/32"]}]},
    )

    def unreadable(interface):
        raise OSError("cannot read config")

    monkeypatch.setattr(tools, "iter_peers", unreadable)
    calls = []
    monkeypatch.setattr(
        tools, "run_command", lambda *args, **kwargs: calls.append((args, kwargs)) or ""
    )

    def fail(*args, **kwargs):
        raise OSError("config write failed")

    monkeypatch.setattr(tools, "upsert_peer", fail)

    with pytest.raises(OSError, match="config write failed"):
        tools.add_peer(
            {
                "name": "peer1",
                "public_key": key,
                "allowed_ips": ["10.0.0.9/32"],
                "preshared_key": new_psk,
            }
        )

    assert calls[0][0][0][5:] == ["allowed-ips", "10.0.0.9/32", "preshared-key", "/dev/stdin"]
    assert calls[0][1]["input_text"] == new_psk
    assert calls[1][0][0][5:] == ["allowed-ips", "10.0.0.2/32"]
    assert calls[1][1]["input_text"] is None


# === wg_remove_peer ===

def test_remove_peer_by_name(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    mods = load_loomwg("tools", "clients_store")
    tools, store = mods["tools"], mods["clients_store"]
    key = _key(5)
    store.upsert("alice", {"public_key": key, "addresses": ["10.0.0.2/32"]})
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(tools, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(tools, "delete_peer", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools, "remove_client_artifacts", lambda public_key: [])

    result = tools.remove_peer({"name": "alice"})

    assert result["data"]["public_key"] == key
    assert store.get("alice") is None


# === wg_list_peers ===

def test_list_peers_reconciles_store_and_live(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    mods = load_loomwg("tools", "clients_store")
    tools, store = mods["tools"], mods["clients_store"]
    tracked, untracked = _key(3), _key(4)
    store.upsert("alice", {"public_key": tracked, "addresses": ["10.0.0.2/32"]})
    monkeypatch.setattr(tools, "active_interfaces", lambda: ["wg0"])
    monkeypatch.setattr(
        tools,
        "read_interface",
        lambda interface: {
            "peers": [
                {
                    "public_key": tracked,
                    "allowed_ips": ["10.0.0.2/32"],
                    "endpoint": None,
                    "latest_handshake": None,
                    "receive_bytes": 0,
                    "transmit_bytes": 0,
                },
                {
                    "public_key": untracked,
                    "allowed_ips": ["10.0.0.3/32"],
                    "endpoint": None,
                    "latest_handshake": None,
                    "receive_bytes": 0,
                    "transmit_bytes": 0,
                },
            ]
        },
    )

    result = tools.list_peers({"interface": "wg0"})

    assert result["data"]["clients"][0]["name"] == "alice"
    assert len(result["data"]["unmanaged"]) == 1


# === wg_export_existing_clients ===

def test_canonical_name_is_filesystem_safe(load_loomwg):
    store = load_loomwg("clients_store")["clients_store"]
    name = store.canonical_name(_key(2))
    assert name.startswith("peer-")
    assert store.NAME_RE.fullmatch(name)
    assert name == store.canonical_name(_key(2))


def test_migrate_legacy_peer_names(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    store = load_loomwg("clients_store")["clients_store"]
    key = _key(2)
    store.upsert("china", {"public_key": _key(9), "addresses": []})
    store.upsert("peer-AAAA1111", {"public_key": key, "addresses": ["10.0.0.2/32"]})

    renamed = store.migrate_legacy_names()

    canonical = store.canonical_name(key)
    assert renamed == {"peer-AAAA1111": canonical}
    assert store.get(canonical)["public_key"] == key
    assert store.get("peer-AAAA1111") is None
    assert store.get("china") is not None


def test_export_existing_clients_imports_and_issues(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    key = _key(2)
    monkeypatch.setattr(
        client,
        "iter_peers",
        lambda interface: [{"public_key": key, "allowed_ips": ["10.0.0.2/32"]}],
    )
    calls = []

    def fake_export(args):
        calls.append(args)
        return {
            "name": args["name"],
            "rotated": True,
            "client_config": str(tmp_path / "clients" / "conf" / f"{args['name']}.conf"),
            "qr_code": str(tmp_path / "clients" / "qr_code" / f"{args['name']}.png"),
        }

    monkeypatch.setattr(client, "export_client", fake_export)

    result = client.export_existing_clients(
        {"interface": "wg0", "endpoint": "1.2.3.4"}
    )

    canonical = client.clients_store.canonical_name(key)
    assert result["imported"] == [canonical]
    assert result["rotated"] == [canonical]
    assert result["clients"][0]["name"] == canonical
    assert calls[0]["name"] == canonical
    assert calls[0]["rotate"] is True


def test_export_existing_clients_rotate_false_skips_keyless(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    key = _key(2)
    monkeypatch.setattr(
        client,
        "iter_peers",
        lambda interface: [{"public_key": key, "allowed_ips": []}],
    )
    monkeypatch.setattr(
        client,
        "export_client",
        lambda args: pytest.fail("export_client should not run for a keyless peer"),
    )

    result = client.export_existing_clients({"interface": "wg0", "rotate": False})

    assert result["imported"] == [client.clients_store.canonical_name(key)]
    assert result["clients"] == []
    assert len(result["failed"]) == 1


def test_export_existing_clients_tool_formats(load_loomwg, monkeypatch):
    tools = load_loomwg("tools")["tools"]
    monkeypatch.setattr(
        tools,
        "export_existing_clients",
        lambda args: {
            "interface": "wg0",
            "clients": [{"name": "peer-abc", "config_ready": True, "qr_ready": True}],
            "imported": ["peer-abc"],
            "rotated": ["peer-abc"],
            "reissued": [],
            "failed": [],
        },
    )

    result = tools.export_existing_clients_tool({"interface": "wg0"})

    assert result["data"]["imported"] == ["peer-abc"]
    assert "peer-abc" in result["content"][0]["text"]


# === client.export_client ===

def test_export_client_reissues_existing(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    conf_dir = tmp_path / "clients" / "conf"
    qr_dir = tmp_path / "clients" / "qr_code"
    conf_dir.mkdir(parents=True)
    qr_dir.mkdir(parents=True)
    (conf_dir / "alice.conf").write_text("[Interface]\nPrivateKey = x\n", encoding="utf-8")
    (qr_dir / "alice.png").write_bytes(b"png")
    client.clients_store.upsert("alice", {"public_key": _key(1), "addresses": ["10.0.0.2/32"]})

    result = client.export_client({"name": "alice"})

    assert result["name"] == "alice"
    assert result["client_config"] == str(conf_dir / "alice.conf")
    assert result["qr_code"] == str(qr_dir / "alice.png")
    assert result["rotated"] is False


def test_migrate_client_layout(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "alice.conf").write_text("[Interface]\n", encoding="utf-8")
    (clients / "alice.png").write_bytes(b"png")

    moved = client.migrate_client_layout()

    assert moved == {"conf": 1, "qr": 1}
    assert (clients / "conf" / "alice.conf").is_file()
    assert (clients / "qr_code" / "alice.png").is_file()
    assert not (clients / "alice.conf").exists()
    assert not (clients / "alice.png").exists()
    assert client.migrate_client_layout() == {"conf": 0, "qr": 0}


def test_migrate_client_layout_keeps_newer_artifact(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    clients = tmp_path / "clients"
    (clients / "conf").mkdir(parents=True)
    (clients / "alice.conf").write_text("legacy\n", encoding="utf-8")
    (clients / "conf" / "alice.conf").write_text("current\n", encoding="utf-8")

    moved = client.migrate_client_layout()

    assert moved == {"conf": 0, "qr": 0}
    assert (clients / "conf" / "alice.conf").read_text(encoding="utf-8") == "current\n"


def test_export_client_rebuilds_missing_config(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert(
        "bob",
        {
            "public_key": _key(2),
            "private_key": _key(3),
            "preshared_key": _key(4),
            "addresses": ["10.0.0.3/32"],
            "endpoint": "1.2.3.4:51820",
        },
    )
    monkeypatch.setattr(
        client,
        "_server_settings",
        lambda interface: (_key(5), 51820, ["10.0.0.0/24"], ["10.0.0.1"]),
    )
    monkeypatch.setattr(
        client,
        "_write_secret",
        lambda path, content: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(content, encoding="utf-8")),
    )
    monkeypatch.setattr(
        client,
        "_generate_qr",
        lambda config, path: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_bytes(b"png"),
        ),
    )

    result = client.export_client({"name": "bob"})

    assert result["rotated"] is False
    assert (tmp_path / "clients" / "conf" / "bob.conf").is_file()


def test_export_client_missing_private_key_errors(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert("ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]})

    with pytest.raises(ValueError, match="rotate=true"):
        client.export_client({"name": "ghost"})


def test_export_client_resolves_doubled_prefix(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    key = _key(6)
    client.clients_store.upsert(f"peer-{key[:12]}", {"public_key": key, "addresses": []})

    with pytest.raises(ValueError, match="rotate=true"):
        client.export_client({"name": f"peer-peer-{key[:12]}"})


def test_export_client_unknown_name_lists_clients(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert("ghost", {"public_key": _key(6), "addresses": []})

    with pytest.raises(ValueError, match="ghost"):
        client.export_client({"name": "nobody"})


def test_export_client_rotate_recreates(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert("ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]})
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        client,
        "create_client",
        lambda args: {
            "name": args["name"],
            "public_key": _key(7),
            "private_key": _key(8),
            "preshared_key": _key(9),
            "addresses": ["10.0.0.9/32"],
            "endpoint": "1.2.3.4:51820",
        },
    )

    result = client.export_client({"name": "ghost", "rotate": True})

    assert result["rotated"] is True


def test_export_client_rotate_restores_peer_on_failure(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert(
        "ghost",
        {
            "public_key": _key(6),
            "addresses": ["10.0.0.9/32"],
            "preshared_key": _key(5),
        },
    )
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    restored: list[tuple] = []
    monkeypatch.setattr(
        client,
        "upsert_peer",
        lambda interface, key, addresses, psk=None: restored.append(
            (interface, key, addresses, psk)
        ),
    )

    def fail_create(args):
        raise ValueError("address conflict")

    monkeypatch.setattr(client, "create_client", fail_create)

    with pytest.raises(ValueError, match="address conflict"):
        client.export_client({"name": "ghost", "rotate": True})

    assert restored == [("wg0", _key(6), ["10.0.0.9/32"], _key(5))]
    record = client.clients_store.get("ghost")
    assert record is not None
    assert record["public_key"] == _key(6)


def test_export_client_rotate_drops_stale_qr_before_recreate(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert(
        "ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]}
    )
    qr_dir = tmp_path / "clients" / "qr_code"
    qr_dir.mkdir(parents=True)
    (qr_dir / "ghost.png").write_bytes(b"old")
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    seen: dict = {}

    def fake_create(args):
        seen["qr_exists"] = client.client_paths(args["name"])[1].exists()
        return {
            "name": args["name"],
            "public_key": _key(7),
            "private_key": _key(8),
            "preshared_key": _key(9),
            "addresses": ["10.0.0.9/32"],
            "endpoint": "1.2.3.4:51820",
        }

    monkeypatch.setattr(client, "create_client", fake_create)

    result = client.export_client({"name": "ghost", "rotate": True})

    assert seen["qr_exists"] is False
    assert result["rotated"] is True


def test_export_client_rotate_removes_new_peer_when_store_write_fails(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert(
        "ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]}
    )
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(client, "upsert_peer", lambda *args, **kwargs: None)
    removed: list[str] = []
    monkeypatch.setattr(
        client, "remove_peer", lambda interface, key: removed.append(key)
    )
    monkeypatch.setattr(
        client,
        "create_client",
        lambda args: {
            "name": args["name"],
            "public_key": _key(7),
            "private_key": _key(8),
            "preshared_key": _key(9),
            "addresses": ["10.0.0.9/32"],
            "endpoint": "1.2.3.4:51820",
        },
    )
    monkeypatch.setattr(
        client,
        "store_client_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        client.export_client({"name": "ghost", "rotate": True})

    assert _key(7) in removed
    record = client.clients_store.get("ghost")
    assert record is not None
    assert record["public_key"] == _key(6)


def test_export_client_rotate_restores_preshared_key_from_config(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    config = client.config_path("wg0")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                "[Interface]",
                f"PrivateKey = {_key(1)}",
                "ListenPort = 51820",
                "",
                "[Peer]",
                f"PublicKey = {_key(6)}",
                "AllowedIPs = 10.0.0.9/32",
                f"PresharedKey = {_key(4)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    # Imported peer: the store record carries no PSK.
    client.clients_store.upsert(
        "ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]}
    )
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(client, "remove_peer", lambda interface, key: None)
    restored: list[tuple] = []
    monkeypatch.setattr(
        client,
        "upsert_peer",
        lambda interface, key, addresses, psk=None: restored.append(
            (interface, key, addresses, psk)
        ),
    )

    def fail_create(args):
        raise ValueError("address conflict")

    monkeypatch.setattr(client, "create_client", fail_create)

    with pytest.raises(ValueError, match="address conflict"):
        client.export_client({"name": "ghost", "rotate": True})

    assert restored == [("wg0", _key(6), ["10.0.0.9/32"], _key(4))]


def test_export_client_rotate_rolls_back_when_cleanup_raises(
    load_loomwg, monkeypatch, tmp_path
):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    client = load_loomwg("client")["client"]
    client.clients_store.upsert(
        "ghost", {"public_key": _key(6), "addresses": ["10.0.0.9/32"]}
    )
    monkeypatch.setattr(client, "run_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(client, "upsert_peer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client,
        "remove_peer",
        lambda interface, key: (_ for _ in ()).throw(OSError("read-only fs"))
        if key == _key(7)
        else None,
    )
    monkeypatch.setattr(
        client,
        "create_client",
        lambda args: {
            "name": args["name"],
            "public_key": _key(7),
            "private_key": _key(8),
            "preshared_key": _key(9),
            "addresses": ["10.0.0.9/32"],
            "endpoint": "1.2.3.4:51820",
        },
    )
    monkeypatch.setattr(
        client,
        "store_client_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    original_drop = client._drop_client_files
    drops = {"count": 0}

    def flaky_drop(name):
        drops["count"] += 1
        if drops["count"] > 1:
            raise PermissionError("denied")
        original_drop(name)

    monkeypatch.setattr(client, "_drop_client_files", flaky_drop)

    with pytest.raises(OSError, match="disk full"):
        client.export_client({"name": "ghost", "rotate": True})

    record = client.clients_store.get("ghost")
    assert record is not None
    assert record["public_key"] == _key(6)


# === wg_get_client_artifact (internal) ===

def test_get_artifact_tool_wraps_result_in_data(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    tools = load_loomwg("tools")["tools"]
    conf_dir = tmp_path / "clients" / "conf"
    conf_dir.mkdir(parents=True)
    (conf_dir / "alice.conf").write_bytes(b"[Interface]\n")
    tools.clients_store.upsert(
        "alice",
        {"public_key": _key(1), "private_key": _key(2), "addresses": ["10.0.0.2/32"]},
    )

    result = tools.get_artifact_tool({"name": "alice", "kind": "config"})

    assert result["content"][0]["text"] == "alice.conf"
    assert base64.b64decode(result["data"]["content_base64"]) == b"[Interface]\n"
    assert result["data"]["media_type"] == "text/plain"


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
        {"public_key": _key(6), "allowed_ips": ["10.0.0.2/32"], "receive_bytes": 100, "transmit_bytes": 300},
        {"public_key": _key(7), "allowed_ips": ["10.0.0.3/32"], "receive_bytes": 50, "transmit_bytes": 50},
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
