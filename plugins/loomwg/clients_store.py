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
