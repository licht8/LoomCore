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


def log_security_event(event: str, **fields: Any) -> None:
    """Log a security-relevant event with structured ``key=value`` fields.

    ``None`` values are dropped so callers can pass optional context freely.
    """
    rendered = " ".join(
        f"{key}={value!r}" for key, value in sorted(fields.items()) if value is not None
    )
    if rendered:
        logger.warning("%s %s", event, rendered)
    else:
        logger.warning("%s", event)
