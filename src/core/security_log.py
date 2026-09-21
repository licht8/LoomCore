"""Security-event logging.

Auth failures, rate-limit hits and rejected requests are written to a dedicated
``loomcore.security`` logger so an operator can route or alert on them without
parsing general application logs. Events never contain credential material.

Records are emitted at WARNING, so they remain visible even when the base
``loomcore`` logger is at WARNING (the normal production level).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("loomcore.security")

_SENSITIVE_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "totp",
)
_REDACTED = "***"


def _render_field(key: str, value: Any) -> str:
    """Render one ``key=value`` field, redacting sensitive keys safely."""
    if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
        return _REDACTED
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - a hostile __repr__ must not break the caller
        return "<unrepresentable>"


def log_security_event(event: str, **fields: Any) -> None:
    """Log a security-relevant event with structured ``key=value`` fields.

    ``None`` values are dropped so callers can pass optional context freely.
    The event name is newline-escaped and known-sensitive field values are
    redacted, so a record can never inject forged log lines or leak credentials.
    """
    safe_event = str(event).replace("\r", "\\r").replace("\n", "\\n")
    parts = [
        f"{key}={_render_field(key, value)}"
        for key, value in sorted(fields.items())
        if value is not None
    ]
    if parts:
        logger.warning("%s %s", safe_event, " ".join(parts))
    else:
        logger.warning("%s", safe_event)
