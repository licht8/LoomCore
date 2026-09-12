"""JSON store for WireGuard clients/users (keyed by name).

Owned by the plugin; lives next to the configs. It holds the public metadata
plus the secrets needed to rebuild a client config (private/preshared keys).
The WireGuard config files remain the source of truth for live state.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from wireguard import config_directory

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_STORE_DIR = ".loomwg"
_STORE_FILE = "clients.json"


def validate_name(value: object) -> str:
    name = str(value or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Client name contains invalid characters")
    return name


def store_path() -> Path:
    return config_directory() / _STORE_DIR / _STORE_FILE


def load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): record
        for name, record in raw.items()
        if isinstance(record, dict)
    }


def save(data: dict[str, dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def get(name: str) -> dict[str, Any] | None:
    return load().get(name)


def resolve_name(value: object) -> str:
    """Resolve a user-supplied name to a stored client key.

    Accepts an exact key, a key with a duplicated ``peer-`` prefix, or a unique
    public-key prefix. Raises ``ValueError`` with the known names otherwise.
    Unlike :func:`validate_name` this is tolerant of the base64 characters that
    ``wg_backfill_clients`` may have baked into ``peer-<shortkey>`` names.
    """
    name = str(value or "").strip()
    if not name:
        raise ValueError("Client name is required")
    data = load()
    if name in data:
        return name

    if name.startswith("peer-peer-"):
        collapsed = name[len("peer-") :]
        if collapsed in data:
            return collapsed

    matches = [
        key
        for key, record in data.items()
        if str(record.get("public_key") or "").startswith(name)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        candidates = ", ".join(sorted(matches))
        raise ValueError(f"Client name '{name}' is ambiguous; candidates: {candidates}")

    known = ", ".join(sorted(data)) or "none"
    raise ValueError(f"Client '{name}' not found. Available clients: {known}")


def upsert(name: str, record: dict[str, Any]) -> None:
    data = load()
    data[name] = {**data.get(name, {}), **record}
    save(data)


def remove(name: str) -> bool:
    data = load()
    if name not in data:
        return False
    del data[name]
    save(data)
    return True


def find_by_public_key(public_key: str) -> str | None:
    for name, record in load().items():
        if record.get("public_key") == public_key:
            return name
    return None


def remove_by_public_key(public_key: str) -> list[str]:
    data = load()
    names = [
        name for name, record in data.items() if record.get("public_key") == public_key
    ]
    if not names:
        return []
    for name in names:
        del data[name]
    save(data)
    return names
