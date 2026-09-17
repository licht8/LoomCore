"""Unit tests for the core plugin manifest loader and validator."""

import json
from pathlib import Path

import pytest

from src.plugins import manifest as manifest_module


def _valid() -> dict:
    return {
        "schema_version": 1,
        "id": "Demo",
        "name": "Demo Plugin",
        "version": "1.2.3",
        "description": "A demo.",
        "entry": "main.py",
        "protocol": "2024-11-05",
        "platforms": ["linux"],
    }


def _write(plugin_dir: Path, data: object) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


# === load_manifest ===


def test_load_manifest_missing_returns_none(tmp_path):
    assert manifest_module.load_manifest(tmp_path) is None


def test_load_manifest_valid(tmp_path):
    _write(tmp_path, _valid())
    assert manifest_module.load_manifest(tmp_path)["id"] == "Demo"


def test_load_manifest_invalid_json_raises(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(manifest_module.ManifestError):
        manifest_module.load_manifest(tmp_path)


def test_load_manifest_non_object_raises(tmp_path):
    (tmp_path / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(manifest_module.ManifestError):
        manifest_module.load_manifest(tmp_path)


# === validate_manifest ===


def test_validate_manifest_good_is_empty():
    assert manifest_module.validate_manifest(_valid(), expected_id="Demo") == []


def test_validate_manifest_id_mismatch():
    issues = manifest_module.validate_manifest(_valid(), expected_id="Other")
    assert any("does not match folder" in issue for issue in issues)


def test_validate_manifest_missing_required_field():
    data = _valid()
    del data["name"]
    assert any("'name'" in issue for issue in manifest_module.validate_manifest(data))


def test_validate_manifest_bad_version():
    data = _valid()
    data["version"] = "1.2"
    assert any("MAJOR.MINOR.PATCH" in issue for issue in manifest_module.validate_manifest(data))


def test_validate_manifest_newer_schema_version():
    data = _valid()
    data["schema_version"] = 2
    assert any("newer than supported" in issue for issue in manifest_module.validate_manifest(data))


def test_validate_manifest_platforms_without_linux():
    data = _valid()
    data["platforms"] = ["win32"]
    assert any("linux" in issue for issue in manifest_module.validate_manifest(data))


def test_validate_manifest_bad_permissions_shape():
    data = _valid()
    data["permissions"] = {"cap_add": "NET_ADMIN", "mounts": [{"source": "/x"}]}
    issues = manifest_module.validate_manifest(data)
    assert any("cap_add must be a list" in issue for issue in issues)
    assert any("source and target" in issue for issue in issues)


def test_validate_manifest_bad_permission_entries():
    data = _valid()
    data["permissions"] = {
        "cap_add": [{"a": 1}],
        "cap_add_optional": [12],
        "mounts": [{"source": 5, "target": ["/x"]}, "nope"],
    }
    issues = manifest_module.validate_manifest(data)
    assert any("cap_add entry" in issue for issue in issues)
    assert any("cap_add_optional entry" in issue for issue in issues)
    assert any("source and target" in issue for issue in issues)


def test_validate_manifest_bad_default_enabled_and_env():
    data = _valid()
    data["default_enabled"] = "yes"
    data["env"] = "LOOM_DEBUG=1"
    issues = manifest_module.validate_manifest(data)
    assert any("default_enabled must be a boolean" in issue for issue in issues)
    assert any("env must be a list" in issue for issue in issues)


# === check_permissions ===


def test_check_permissions_missing_capability(monkeypatch):
    monkeypatch.setattr(manifest_module, "effective_capabilities", lambda: 0)
    data = _valid()
    data["permissions"] = {"cap_add": ["NET_ADMIN"]}
    warnings = manifest_module.check_permissions(data)
    assert any("NET_ADMIN" in warning for warning in warnings)


def test_check_permissions_capability_present(monkeypatch):
    monkeypatch.setattr(manifest_module, "effective_capabilities", lambda: 1 << 12)
    data = _valid()
    data["permissions"] = {"cap_add": ["NET_ADMIN"]}
    assert manifest_module.check_permissions(data) == []


def test_check_permissions_missing_mount(tmp_path):
    data = _valid()
    data["permissions"] = {
        "mounts": [{"source": "/nope", "target": str(tmp_path / "gone"), "mode": "rw"}]
    }
    warnings = manifest_module.check_permissions(data)
    assert any("is missing" in warning for warning in warnings)


def test_check_permissions_ignores_malformed_entries(monkeypatch):
    monkeypatch.setattr(manifest_module, "effective_capabilities", lambda: None)
    data = _valid()
    data["permissions"] = {
        "cap_add": [{"a": 1}, 12],
        "mounts": [{"source": "/x", "target": 5}, "nope"],
    }

    warnings = manifest_module.check_permissions(data)

    assert warnings == [
        "capability entry {'a': 1} is not a string",
        "capability entry 12 is not a string",
    ]


def test_check_permissions_reports_non_list_capabilities(monkeypatch):
    monkeypatch.setattr(manifest_module, "effective_capabilities", lambda: None)
    data = _valid()
    data["permissions"] = {"cap_add": "NET_ADMIN"}

    assert manifest_module.check_permissions(data) == [
        "permissions.cap_add must be a list, got str"
    ]


def test_check_permissions_reports_non_list_mounts(monkeypatch):
    monkeypatch.setattr(manifest_module, "effective_capabilities", lambda: None)
    data = _valid()
    data["permissions"] = {"mounts": 5}

    assert manifest_module.check_permissions(data) == [
        "permissions.mounts must be a list, got int"
    ]


def test_check_permissions_host_network_heuristic(monkeypatch):
    monkeypatch.setattr(manifest_module.socket, "gethostname", lambda: "a1b2c3d4e5f6")
    data = _valid()
    data["permissions"] = {"network_mode": "host"}
    warnings = manifest_module.check_permissions(data)
    assert any("bridge-networked" in warning for warning in warnings)


# === pid namespace + SYSLOG ===


def test_syslog_is_a_known_capability():
    assert manifest_module.CAPABILITIES["SYSLOG"] == 34


def test_validate_manifest_accepts_pid_host():
    data = _valid()
    data["permissions"] = {"pid": "host"}
    assert manifest_module.validate_manifest(data) == []


def test_validate_manifest_rejects_bad_pid():
    data = _valid()
    data["permissions"] = {"pid": "maybe"}
    issues = manifest_module.validate_manifest(data)
    assert any("permissions.pid must be" in issue for issue in issues)


def _fake_proc1(root: Path, name: str | None) -> None:
    (root / "1").mkdir(parents=True, exist_ok=True)
    if name is not None:
        (root / "1" / "comm").write_text(f"{name}\n", encoding="utf-8")


def test_host_pid_looks_active_true(monkeypatch, tmp_path):
    _fake_proc1(tmp_path, "systemd")
    monkeypatch.setattr(manifest_module, "PROC_ROOT", tmp_path)
    assert manifest_module.host_pid_looks_active() is True


def test_host_pid_looks_active_false(monkeypatch, tmp_path):
    _fake_proc1(tmp_path, "uvicorn")
    monkeypatch.setattr(manifest_module, "PROC_ROOT", tmp_path)
    assert manifest_module.host_pid_looks_active() is False


def test_host_pid_looks_active_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(manifest_module, "PROC_ROOT", tmp_path)
    assert manifest_module.host_pid_looks_active() is False


def test_host_pid_looks_active_status_fallback(monkeypatch, tmp_path):
    (tmp_path / "1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "1" / "status").write_text("Name:\tsystemd\nState:\tS\n", encoding="utf-8")
    monkeypatch.setattr(manifest_module, "PROC_ROOT", tmp_path)
    assert manifest_module.host_pid_looks_active() is True


def test_check_permissions_warns_on_missing_host_pid(monkeypatch):
    monkeypatch.setattr(manifest_module, "host_pid_looks_active", lambda: False)
    data = _valid()
    data["permissions"] = {"pid": "host"}
    warnings = manifest_module.check_permissions(data)
    assert any("PID namespace" in warning for warning in warnings)


def test_check_permissions_pid_host_silent_when_active(monkeypatch):
    monkeypatch.setattr(manifest_module, "host_pid_looks_active", lambda: True)
    data = _valid()
    data["permissions"] = {"pid": "host"}
    assert manifest_module.check_permissions(data) == []


# === check_compatibility ===


def test_check_compatibility_compatible():
    data = _valid()
    data["requires_core"] = ">=0.0.1"
    assert manifest_module.check_compatibility(data, "0.0.1") == []


def test_check_compatibility_unsatisfied_requires_core():
    data = _valid()
    data["requires_core"] = ">=9.9.9"
    issues = manifest_module.check_compatibility(data, "0.0.1")
    assert any("requires core >=9.9.9" in issue for issue in issues)


def test_check_compatibility_unsupported_protocol():
    data = _valid()
    data["protocol"] = "1999-01-01"
    issues = manifest_module.check_compatibility(data, "0.0.1")
    assert any("unsupported protocol" in issue for issue in issues)


def test_check_compatibility_invalid_specifier():
    data = _valid()
    data["requires_core"] = "not a specifier"
    issues = manifest_module.check_compatibility(data, "0.0.1")
    assert any("not a valid version specifier" in issue for issue in issues)


def test_check_compatibility_ignores_missing_requires_core():
    assert manifest_module.check_compatibility(_valid(), "0.0.1") == []


# === PluginManifest view ===


def test_plugin_manifest_from_dict_defaults():
    view = manifest_module.PluginManifest.from_dict({"id": "Demo"})
    assert view.id == "Demo"
    assert view.entry == "main.py"
    assert view.default_enabled is True
    assert view.permissions == {}
