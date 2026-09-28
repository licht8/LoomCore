"""Manifest loading and runtime permission validation for the LoomWG plugin.

The manifest is shared metadata (see docs/architecture/PLUGIN_MANIFEST.md).
The core reads and validates the manifest during discovery; this plugin
re-validates it against its own runtime and warns about mismatches so a
misconfigured deployment is visible immediately.
Validation never blocks startup — it only logs/propagates warnings.
"""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"
PLUGIN_ID = MANIFEST_PATH.parent.name

_REQUIRED_FIELDS = ("id", "name", "version", "description", "entry", "protocol")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_KNOWN_SCHEMA_VERSION = 1

# Linux capability numbers (see <linux/capability.h>).
_CAPABILITIES = {"NET_ADMIN": 12, "NET_RAW": 13, "SYS_MODULE": 16}

# Docker sets the hostname to the short container id (12 hex chars) on bridge
# networks; host networking keeps the host's hostname.
_CONTAINER_HOSTNAME_RE = re.compile(r"^[0-9a-f]{12}$")


class ManifestError(ValueError):
    """Raised when the manifest is missing or is not a valid JSON object."""


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load the plugin manifest (defaults to `plugins/<id>/manifest.json`)."""
    manifest_path = path or MANIFEST_PATH
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid manifest JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("Manifest must be a JSON object")
    return data


def validate_manifest(
    data: dict[str, Any], *, expected_id: str | None = None
) -> list[str]:
    """Return a list of schema problems (empty when the manifest is valid)."""
    issues: list[str] = []
    for field in _REQUIRED_FIELDS:
        value = data.get(field)
        if not value or not isinstance(value, str):
            issues.append(f"missing or invalid '{field}'")

    if expected_id and data.get("id") != expected_id:
        issues.append(f"id '{data.get('id')}' does not match folder '{expected_id}'")

    version = data.get("version")
    if isinstance(version, str) and version and not _VERSION_RE.match(version):
        issues.append(f"version '{version}' is not MAJOR.MINOR.PATCH")

    schema_version = data.get("schema_version", _KNOWN_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        issues.append("schema_version must be an integer")
    elif schema_version > _KNOWN_SCHEMA_VERSION:
        issues.append(
            f"schema_version {schema_version} is newer than supported "
            f"({_KNOWN_SCHEMA_VERSION})"
        )

    platforms = data.get("platforms")
    if platforms is not None and (
        not isinstance(platforms, list)
        or not all(isinstance(item, str) for item in platforms)
    ):
        issues.append("platforms must be a list of strings")

    permissions = data.get("permissions")
    if permissions is not None:
        issues.extend(_validate_permissions(permissions))

    env = data.get("env")
    if env is not None and not isinstance(env, list):
        issues.append("env must be a list")
    return issues


def _validate_permissions(permissions: Any) -> list[str]:
    if not isinstance(permissions, dict):
        return ["permissions must be an object"]
    issues: list[str] = []
    for field_name in ("cap_add", "cap_add_optional"):
        entries = permissions.get(field_name, [])
        if not isinstance(entries, list):
            issues.append(f"permissions.{field_name} must be a list")
            continue
        for entry in entries:
            if not isinstance(entry, str):
                issues.append(
                    f"permissions.{field_name} entry {entry!r} is not a string"
                )
    mounts = permissions.get("mounts", [])
    if not isinstance(mounts, list):
        issues.append("permissions.mounts must be a list")
    else:
        for mount in mounts:
            source = mount.get("source") if isinstance(mount, dict) else None
            target = mount.get("target") if isinstance(mount, dict) else None
            if not (
                isinstance(source, str)
                and source
                and isinstance(target, str)
                and target
            ):
                issues.append("each permissions.mounts entry needs source and target")
    return issues


def _effective_capabilities() -> int | None:
    """Read the effective capability bitmask from /proc/self/status."""
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("CapEff:"):
            try:
                return int(line.split()[1], 16)
            except (IndexError, ValueError):
                return None
    return None


def _permissions(manifest: dict[str, Any]) -> dict[str, Any] | None:
    permissions = manifest.get("permissions") or {}
    return permissions if isinstance(permissions, dict) else None


def _capability_issues(
    names: Any,
    caps: int | None,
    *,
    optional: bool = False,
    field: str = "cap_add",
) -> list[str]:
    issues: list[str] = []
    label = "optional capability" if optional else "capability"
    if names is not None and not isinstance(names, list):
        return [f"permissions.{field} must be a list, got {type(names).__name__}"]
    for name in names or []:
        if not isinstance(name, str):
            issues.append(f"capability entry {name!r} is not a string")
            continue
        bit = _CAPABILITIES.get(name)
        if bit is None:
            issues.append(f"unknown capability '{name}'")
        elif caps is not None and not caps & (1 << bit):
            issues.append(f"{label} {name} is not effective in this container")
    return issues


def _mount_issues(mounts: Any) -> list[str]:
    issues: list[str] = []
    if mounts is not None and not isinstance(mounts, list):
        return [f"permissions.mounts must be a list, got {type(mounts).__name__}"]
    for mount in mounts or []:
        if not isinstance(mount, dict):
            continue
        target = mount.get("target")
        mode = mount.get("mode", "rw")
        if not isinstance(target, str) or not target:
            continue
        path = Path(target)
        if not path.exists():
            issues.append(f"mounted path {target} is missing")
            continue
        if mode == "rw" and not os.access(path, os.W_OK):
            issues.append(f"mounted path {target} is not writable")
        elif mode == "ro" and not os.access(path, os.R_OK):
            issues.append(f"mounted path {target} is not readable")
    return issues


def check_permissions(manifest: dict[str, Any]) -> list[str]:
    """Compare required permissions with the plugin's actual runtime."""
    permissions = _permissions(manifest)
    if permissions is None:
        return ["permissions is not an object"]

    caps = _effective_capabilities()
    warnings = _capability_issues(permissions.get("cap_add", []), caps)
    warnings.extend(_mount_issues(permissions.get("mounts", [])))
    if permissions.get("network_mode") == "host" and _CONTAINER_HOSTNAME_RE.match(
        socket.gethostname()
    ):
        warnings.append(
            "network_mode 'host' is declared but the container looks bridge-networked"
        )
    return warnings


def check_optional_permissions(manifest: dict[str, Any]) -> list[str]:
    """Notes for declared capabilities the plugin can run without and lacks."""
    permissions = _permissions(manifest)
    if permissions is None:
        return []
    return _capability_issues(
        permissions.get("cap_add_optional", []),
        _effective_capabilities(),
        optional=True,
        field="cap_add_optional",
    )


def runtime_report() -> dict[str, Any]:
    """Full self-check: load, validate against the folder name, check permissions."""
    try:
        manifest = load_manifest()
    except ManifestError as exc:
        return {
            "manifest": None,
            "issues": [str(exc)],
            "permission_warnings": [],
            "permission_notes": [],
        }
    return {
        "manifest": manifest,
        "issues": validate_manifest(manifest, expected_id=PLUGIN_ID),
        "permission_warnings": check_permissions(manifest),
        "permission_notes": check_optional_permissions(manifest),
    }
