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


def test_check_permissions_host_network_heuristic(monkeypatch):
    monkeypatch.setattr(manifest_module.socket, "gethostname", lambda: "a1b2c3d4e5f6")
    data = _valid()
    data["permissions"] = {"network_mode": "host"}
    warnings = manifest_module.check_permissions(data)
    assert any("bridge-networked" in warning for warning in warnings)


# === PluginManifest view ===


def test_plugin_manifest_from_dict_defaults():
    view = manifest_module.PluginManifest.from_dict({"id": "Demo"})
    assert view.id == "Demo"
    assert view.entry == "main.py"
    assert view.default_enabled is True
    assert view.permissions == {}
