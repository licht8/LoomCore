"""System operations helpers for the LoomOps plugin."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Audit entries are best-effort: a mutating call must never crash the plugin just
# because the log path is not writable.
DEFAULT_AUDIT_LOG = Path(__file__).resolve().parents[2] / "tmp" / "loomops-audit.log"


def audit_log_path() -> Path:
    configured = os.environ.get("LOOMOPS_AUDIT_LOG", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_AUDIT_LOG


def audit_event(event: str, **fields: Any) -> str | None:
    """Append one JSON line to the audit log; return an error string on failure."""
    # Caller fields go first so the canonical identity fields always win.
    record = {**fields, "timestamp": datetime.now(tz=UTC).isoformat(), "event": event}
    path = audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        return str(exc)
    return None


def bounded_int(value: Any, default: int, maximum: int, minimum: int = 1) -> int:
    """Coerce ``value`` to an int within ``[minimum, maximum]`` or return ``default``."""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(number, maximum))
