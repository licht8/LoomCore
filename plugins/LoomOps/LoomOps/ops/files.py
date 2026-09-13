"""Confined file access rooted in LOOMOPS_ALLOWED_PATHS."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from . import audit_event

MAX_READ_BYTES = 256_000


def allowed_paths() -> list[Path]:
    """Resolved roots the plugin may read/write (empty means deny all)."""
    raw = os.environ.get("LOOMOPS_ALLOWED_PATHS", "")
    roots: list[Path] = []
    for item in raw.replace(os.pathsep, ",").split(","):
        item = item.strip()
        if item:
            roots.append(Path(item).expanduser().resolve())
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
    except OSError as exc:
        raise ValueError(f"Invalid path: {exc}") from exc
    if any(candidate == root or root in candidate.parents for root in roots):
        return candidate
    raise ValueError(f"Path '{raw}' is outside the allowed paths")


def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(arguments.get("path"))
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(MAX_READ_BYTES)
    truncated = size > MAX_READ_BYTES
    content = raw.decode("utf-8", errors="replace")
    if truncated:
        content += f"\n... [truncated at {MAX_READ_BYTES} of {size} bytes]"
    return {
        "output": content,
        "path": str(path),
        "size": size,
        "truncated": truncated,
    }


def write_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(arguments.get("path"))
    content = arguments.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if path.exists() and path.is_dir():
        raise ValueError(f"Path is a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise RuntimeError(f"Failed to write {path}: {exc}") from exc
    written = len(content.encode("utf-8"))
    audit_error = audit_event("ops_write_file", path=str(path), bytes=written)
    return {
        "output": f"Wrote {written} bytes to {path}",
        "path": str(path),
        "bytes": written,
        "audit_error": audit_error,
    }
