"""Confined file access rooted in LOOMOPS_ALLOWED_PATHS."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import audit_event, confine

MAX_READ_BYTES = 256_000


def allowed_paths() -> list[Path]:
    """Resolved roots the plugin may read/write (empty means deny all)."""
    raw = os.environ.get("LOOMOPS_ALLOWED_PATHS", "")
    roots: list[Path] = []
    for item in raw.replace(os.pathsep, ",").split(","):
        item = item.strip()
        if item:
            try:
                roots.append(Path(item).expanduser().resolve())
            except (OSError, RuntimeError) as exc:
                raise ValueError(
                    f"Allowed path is not resolvable: {item}: {exc}"
                ) from exc
    return roots


def resolve_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    roots = allowed_paths()
    if not roots:
        raise ValueError(
            "No paths are allowed; set LOOMOPS_ALLOWED_PATHS (comma or ':' separated)"
        )
    try:
        candidate = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid path: {exc}") from exc
    if any(candidate == root or root in candidate.parents for root in roots):
        return candidate
    raise ValueError(f"Path '{raw}' is outside the allowed paths")


def _confined_root(path: Path, raw: Any) -> Path:
    root = confine.matching_root(path, allowed_paths())
    if root is None:
        raise ValueError(f"Path '{raw}' is outside the allowed paths")
    return root


def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("path")
    path = resolve_path(raw)
    root = _confined_root(path, raw)
    try:
        fd, info = confine.open_read(root, path)
    except FileNotFoundError:
        raise ValueError(f"File not found: {path}") from None
    with confine.open_fd(fd, "rb") as handle:
        raw_bytes = handle.read(MAX_READ_BYTES)
    size = info.st_size
    truncated = size > MAX_READ_BYTES
    content = raw_bytes.decode("utf-8", errors="replace")
    if truncated:
        content += f"\n... [truncated at {MAX_READ_BYTES} of {size} bytes]"
    return {
        "output": content,
        "path": str(path),
        "size": size,
        "truncated": truncated,
    }


def write_file(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("path")
    path = resolve_path(raw)
    content = arguments.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    root = _confined_root(path, raw)
    data = content.encode("utf-8")
    try:
        confine.write_atomic(root, path, data)
    except OSError as exc:
        raise RuntimeError(f"Failed to write {path}: {exc}") from exc
    written = len(data)
    audit_error = audit_event("ops_write_file", path=str(path), bytes=written)
    return {
        "output": f"Wrote {written} bytes to {path}",
        "path": str(path),
        "bytes": written,
        "audit_error": audit_error,
    }
