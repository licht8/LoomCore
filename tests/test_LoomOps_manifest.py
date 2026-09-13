"""LoomOps manifest is valid for the core loader (identity, prefix, env)."""

from pathlib import Path

from src.plugins.manifest import check_permissions, load_manifest, validate_manifest

LOOMOPS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "LoomOps"


def test_manifest_is_valid():
    data = load_manifest(LOOMOPS_DIR)
    assert data is not None
    assert validate_manifest(data, expected_id="LoomOps") == []
    assert data["id"] == "LoomOps"
    assert data["tools_prefix"] == "ops_"
    assert data["default_enabled"] is True
    assert data["platforms"] == ["linux"]


def test_manifest_declares_expected_env():
    data = load_manifest(LOOMOPS_DIR)
    env_names = {item["name"] for item in data["env"]}
    assert {
        "LOOMOPS_ALLOWED_COMMANDS",
        "LOOMOPS_ALLOWED_PATHS",
        "LOOMOPS_AUDIT_LOG",
        "LOOMOPS_COMMAND_TIMEOUT",
        "LOOMOPS_LOG_ROOTS",
        "LOOMOPS_MAX_LOG_LINES",
        "LOOMOPS_CPU_THRESHOLD",
        "LOOMOPS_DISK_THRESHOLD",
        "LOOMOPS_LOAD_THRESHOLD",
    } <= env_names


def test_manifest_declares_host_observability():
    data = load_manifest(LOOMOPS_DIR)
    permissions = data["permissions"]
    assert permissions["pid"] == "host"
    assert "SYSLOG" in permissions["cap_add"]
    assert "SYS_PTRACE" not in permissions["cap_add"]
    assert "DAC_READ_SEARCH" not in permissions["cap_add"]
    targets = {mount["target"] for mount in permissions["mounts"]}
    assert "/host/var/log" in targets
    assert all(mount["mode"] == "ro" for mount in permissions["mounts"])


def test_manifest_permissions_check_returns_list():
    data = load_manifest(LOOMOPS_DIR)
    assert isinstance(check_permissions(data), list)
