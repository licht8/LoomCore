"""Tests for the LoomWG plugin manifest and its runtime validation."""

from __future__ import annotations

import json


def test_manifest_loads_from_plugin_dir(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]

    data = manifest_module.load_manifest()

    assert data["id"] == "LoomWG"
    assert data["entry"] == "main.py"
    assert data["tools_prefix"] == "wg_"
    assert data["permissions"]["network_mode"] == "host"


def test_runtime_report_has_no_schema_issues(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]

    report = manifest_module.runtime_report()

    assert report["issues"] == []
    assert report["manifest"]["id"] == "LoomWG"


def test_validate_manifest_flags_missing_fields(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]

    issues = manifest_module.validate_manifest({"id": "LoomWG"})

    assert any("name" in issue for issue in issues)
    assert any("version" in issue for issue in issues)
    assert any("entry" in issue for issue in issues)


def test_validate_manifest_flags_id_mismatch(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = manifest_module.load_manifest()

    issues = manifest_module.validate_manifest(data, expected_id="Other")

    assert any("does not match folder" in issue for issue in issues)


def test_validate_manifest_flags_bad_version(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = {
        "id": "LoomWG",
        "name": "WireGuard",
        "version": "1.0",
        "description": "test",
        "entry": "main.py",
        "protocol": "2024-11-05",
    }

    issues = manifest_module.validate_manifest(data)

    assert any("MAJOR.MINOR.PATCH" in issue for issue in issues)


def test_validate_manifest_flags_newer_schema(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = manifest_module.load_manifest()
    data["schema_version"] = manifest_module._KNOWN_SCHEMA_VERSION + 1

    issues = manifest_module.validate_manifest(data)

    assert any("schema_version" in issue for issue in issues)


def test_validate_manifest_flags_bad_platforms(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = manifest_module.load_manifest()
    data["platforms"] = "linux"

    issues = manifest_module.validate_manifest(data)

    assert any("platforms" in issue for issue in issues)


def test_optional_capability_is_a_note_not_a_warning(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(
        manifest_module,
        "_effective_capabilities",
        lambda: (1 << 12) | (1 << 13),
    )
    manifest = {
        "permissions": {
            "cap_add": ["NET_ADMIN", "NET_RAW"],
            "cap_add_optional": ["SYS_MODULE"],
        }
    }

    assert manifest_module.check_permissions(manifest) == []
    notes = manifest_module.check_optional_permissions(manifest)
    assert len(notes) == 1
    assert notes[0].startswith("optional capability SYS_MODULE")


def test_runtime_report_exposes_optional_notes(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]

    report = manifest_module.runtime_report()

    assert isinstance(report["permission_notes"], list)


def test_check_permissions_warns_when_cap_missing(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: 1 << 13)
    monkeypatch.setattr(manifest_module.Path, "exists", lambda self: True)
    monkeypatch.setattr(manifest_module.os, "access", lambda path, mode: True)
    manifest = {
        "permissions": {
            "cap_add": ["NET_ADMIN"],
            "mounts": [],
            "network_mode": "bridge",
        }
    }

    warnings = manifest_module.check_permissions(manifest)

    assert any("NET_ADMIN" in warning for warning in warnings)


def test_check_permissions_clean_when_all_present(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(
        manifest_module,
        "_effective_capabilities",
        lambda: (1 << 12) | (1 << 13) | (1 << 16),
    )
    monkeypatch.setattr(manifest_module.Path, "exists", lambda self: True)
    monkeypatch.setattr(manifest_module.os, "access", lambda path, mode: True)
    monkeypatch.setattr(manifest_module.socket, "gethostname", lambda: "test-host")

    warnings = manifest_module.check_permissions(manifest_module.load_manifest())

    assert warnings == []


def test_check_permissions_warns_on_missing_mount(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)
    monkeypatch.setattr(manifest_module.Path, "exists", lambda self: False)
    manifest = {
        "permissions": {
            "mounts": [{"source": "/src", "target": "/missing", "mode": "rw"}]
        }
    }

    warnings = manifest_module.check_permissions(manifest)

    assert any("/missing" in warning for warning in warnings)


def test_validate_manifest_flags_non_string_capabilities(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = manifest_module.load_manifest()
    data["permissions"] = {"cap_add": [{"a": 1}], "cap_add_optional": [12]}

    issues = manifest_module.validate_manifest(data)

    assert any("cap_add entry" in issue for issue in issues)
    assert any("cap_add_optional entry" in issue for issue in issues)


def test_validate_manifest_flags_non_string_mount_fields(load_loomwg):
    manifest_module = load_loomwg("manifest")["manifest"]
    data = manifest_module.load_manifest()
    data["permissions"] = {"mounts": [{"source": 5, "target": ["/etc/wireguard"]}]}

    issues = manifest_module.validate_manifest(data)

    assert any("needs source and target" in issue for issue in issues)


def test_check_permissions_skips_malformed_entries(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)
    manifest = {
        "permissions": {
            "cap_add": [{"a": 1}, 12],
            "mounts": [{"source": "/src", "target": 5}, "not-a-mount"],
        }
    }

    warnings = manifest_module.check_permissions(manifest)

    assert warnings == [
        "capability entry {'a': 1} is not a string",
        "capability entry 12 is not a string",
    ]


def test_check_permissions_reports_non_list_capabilities(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)

    warnings = manifest_module.check_permissions(
        {"permissions": {"cap_add": "NET_ADMIN"}}
    )

    assert warnings == ["permissions.cap_add must be a list, got str"]


def test_check_permissions_reports_non_list_mounts(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)

    warnings = manifest_module.check_permissions({"permissions": {"mounts": 5}})

    assert warnings == ["permissions.mounts must be a list, got int"]


def test_check_optional_permissions_names_the_field(load_loomwg, monkeypatch):
    manifest_module = load_loomwg("manifest")["manifest"]
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)

    notes = manifest_module.check_optional_permissions(
        {"permissions": {"cap_add_optional": 5}}
    )

    assert notes == ["permissions.cap_add_optional must be a list, got int"]


def test_runtime_report_survives_malformed_entries(load_loomwg, monkeypatch, tmp_path):
    manifest_module = load_loomwg("manifest")["manifest"]
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "id": "LoomWG",
                "name": "WireGuard",
                "version": "1.0.0",
                "description": "test",
                "entry": "main.py",
                "protocol": "2024-11-05",
                "permissions": {"cap_add": [{"a": 1}], "mounts": [{"target": 5}]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", path)
    monkeypatch.setattr(manifest_module, "_effective_capabilities", lambda: None)

    report = manifest_module.runtime_report()

    assert report["manifest"]["id"] == "LoomWG"
    assert any("cap_add entry" in issue for issue in report["issues"])
    assert report["permission_warnings"] == [
        "capability entry {'a': 1} is not a string"
    ]
