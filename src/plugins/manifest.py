"""Manifest loading and validation for LoomCore plugins.

The core reads each plugin's ``manifest.json`` to learn its identity, version
and default-enabled state (see ``docs/architecture/PLUGIN_MANIFEST.md``).
Validation only warns: a manifest mismatch never blocks startup.
"""

from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet

MANIFEST_FILENAME = "manifest.json"

_REQUIRED_FIELDS = ("id", "name", "version", "description", "entry", "protocol")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_KNOWN_SCHEMA_VERSION = 1

# MCP protocol revisions this core speaks. A plugin declaring anything else is
# not launched (surfaced in plugin status instead of failing startup).
SUPPORTED_PROTOCOLS = frozenset({"2024-11-05"})

# Linux capability numbers (see <linux/capability.h>).
CAPABILITIES = {"NET_ADMIN": 12, "NET_RAW": 13, "SYS_MODULE": 16, "SYSLOG": 34}

PROC_ROOT = Path("/proc")

# Docker sets the hostname to the short container id (12 hex chars) on bridge
# networks; host networking keeps the host's hostname.
_CONTAINER_HOSTNAME_RE = re.compile(r"^[0-9a-f]{12}$")

# PID 1 names that indicate the host PID namespace (`pid: host`) is mounted.
_HOST_INIT_NAMES = {"systemd", "init"}


class ManifestError(ValueError):
    """Raised when a manifest cannot be read or is not a valid JSON object."""


@dataclass
class PluginManifest:
    """Structured view of a plugin manifest."""

    id: str
    name: str = ""
    version: str = ""
    description: str = ""
    entry: str = "main.py"
    protocol: str = ""
    tools_prefix: str = ""
    requires_core: str = ""
    permissions: dict[str, Any] = field(default_factory=dict)
    env: list[Any] = field(default_factory=list)
    internal_tools: list[str] = field(default_factory=list)
    default_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """Build a manifest view from a parsed dict, ignoring unknown fields."""
        permissions = data.get("permissions")
        env = data.get("env")
        internal_tools = data.get("internal_tools")
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            version=str(data.get("version") or ""),
            description=str(data.get("description") or ""),
            entry=str(data.get("entry") or "main.py"),
            protocol=str(data.get("protocol") or ""),
            tools_prefix=str(data.get("tools_prefix") or ""),
            requires_core=str(data.get("requires_core") or ""),
            permissions=permissions if isinstance(permissions, dict) else {},
            env=env if isinstance(env, list) else [],
            internal_tools=internal_tools if isinstance(internal_tools, list) else [],
            default_enabled=bool(data.get("default_enabled", True)),
        )


def load_manifest(plugin_dir: Path | str) -> dict[str, Any] | None:
    """Load ``<plugin_dir>/manifest.json``; return None when it is absent.

    Raises:
        ManifestError: the file exists but cannot be read or is not a JSON object.
    """
    manifest_path = Path(plugin_dir) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid manifest JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    return data


def validate_manifest(
    data: dict[str, Any], *, expected_id: str | None = None
) -> list[str]:
    """Return a list of schema problems (empty when the manifest is valid)."""
    issues: list[str] = []
    for field_name in _REQUIRED_FIELDS:
        value = data.get(field_name)
        if not value or not isinstance(value, str):
            issues.append(f"missing or invalid '{field_name}'")

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
    if platforms is not None:
        if not isinstance(platforms, list) or not all(
            isinstance(item, str) for item in platforms
        ):
            issues.append("platforms must be a list of strings")
        elif "linux" not in platforms:
            issues.append("platforms does not include 'linux'")

    permissions = data.get("permissions")
    if permissions is not None:
        issues.extend(_validate_permissions(permissions))

    env = data.get("env")
    if env is not None and not isinstance(env, list):
        issues.append("env must be a list")

    if "default_enabled" in data and not isinstance(data["default_enabled"], bool):
        issues.append("default_enabled must be a boolean")

    if "tools_prefix" in data and not isinstance(data["tools_prefix"], str):
        issues.append("tools_prefix must be a string")

    if "internal_tools" in data and not isinstance(data["internal_tools"], list):
        issues.append("internal_tools must be a list")
    return issues


def check_compatibility(manifest: dict[str, Any], core_version: str) -> list[str]:
    """Return reasons the plugin cannot run against this core (empty = compatible).

    Checks the declared ``protocol`` revision against :data:`SUPPORTED_PROTOCOLS`
    and ``requires_core`` (a PEP 440 specifier) against ``core_version``. A bad
    declaration counts as incompatible so a broken plugin is surfaced rather
    than launched.
    """
    issues: list[str] = []

    protocol = manifest.get("protocol")
    if isinstance(protocol, str) and protocol and protocol not in SUPPORTED_PROTOCOLS:
        supported = ", ".join(sorted(SUPPORTED_PROTOCOLS))
        issues.append(f"unsupported protocol '{protocol}' (supported: {supported})")

    requires = manifest.get("requires_core")
    if isinstance(requires, str) and requires.strip():
        try:
            specifier = SpecifierSet(requires)
        except InvalidSpecifier:
            issues.append(f"requires_core '{requires}' is not a valid version specifier")
        else:
            if core_version not in specifier:
                issues.append(
                    f"requires core {requires}, but this core is {core_version}"
                )
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
    pid = permissions.get("pid")
    if pid is not None and pid not in ("host", "container"):
        issues.append("permissions.pid must be 'host' or 'container'")
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


def effective_capabilities() -> int | None:
    """Read the effective capability bitmask from /proc/self/status."""
    try:
        status = (PROC_ROOT / "self" / "status").read_text(encoding="utf-8")
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
        bit = CAPABILITIES.get(name)
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


def host_network_looks_active() -> bool:
    """Heuristic: host networking keeps the host's hostname, bridges do not."""
    return not bool(_CONTAINER_HOSTNAME_RE.match(socket.gethostname()))


def host_pid_looks_active() -> bool:
    """Heuristic: with ``pid: host`` init is systemd/init, not the container's."""
    try:
        name = (PROC_ROOT / "1" / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    if name:
        return name in _HOST_INIT_NAMES
    try:
        status = (PROC_ROOT / "1" / "status").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in status.splitlines():
        if line.startswith("Name:"):
            return line.partition(":")[2].strip() in _HOST_INIT_NAMES
    return False


def check_permissions(manifest: dict[str, Any]) -> list[str]:
    """Compare required permissions with the core's actual runtime."""
    permissions = _permissions(manifest)
    if permissions is None:
        return ["permissions is not an object"]

    caps = effective_capabilities()
    warnings = _capability_issues(permissions.get("cap_add", []), caps)
    warnings.extend(_mount_issues(permissions.get("mounts", [])))
    if permissions.get("network_mode") == "host" and not host_network_looks_active():
        warnings.append(
            "network_mode 'host' is declared but the container looks bridge-networked"
        )
    if permissions.get("pid") == "host" and not host_pid_looks_active():
        warnings.append(
            "pid namespace 'host' is declared but /proc does not look like the "
            "host PID namespace"
        )
    return warnings
