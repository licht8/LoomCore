"""Gated latency instrumentation for the agent pipeline.

Timings are emitted only when ``LOOMCORE_TIMING`` is truthy (see Settings);
with the flag off every call here is a cheap no-op, so production logs stay
clean. Each stage is one structured line: ``[timing] stage=<name> dur=<s>``
followed by the caller-supplied fields.
"""

import logging
import time
from contextlib import asynccontextmanager

from src.config import settings

logger = logging.getLogger("loomcore.timing")


def enabled() -> bool:
    """Whether latency instrumentation is switched on."""
    return settings.timing_enabled


def _sanitize(value: object) -> str:
    """Neutralise CR/LF to spaces so a field cannot forge a log line."""
    return str(value).replace(chr(13), " ").replace(chr(10), " ")


def _render_field(key: str, value: object) -> str:
    """Render one ``key=value`` pair with CR/LF neutralised to spaces."""
    return f"{_sanitize(key)}={_sanitize(value)}"


def log_stage(name: str, dur: float, **fields) -> None:
    """Emit one structured timing line (no-op when disabled)."""
    if not enabled():
        return
    extra = " ".join(_render_field(key, value) for key, value in fields.items())
    line = f"[timing] stage={_sanitize(name)} dur={dur:.3f}s"
    if extra:
        line = f"{line} {extra}"
    logger.info(line)


@asynccontextmanager
async def span(name: str, **fields):
    """Time an ``async with`` block and log it on exit (even on error)."""
    started = time.monotonic()
    try:
        yield
    finally:
        log_stage(name, time.monotonic() - started, **fields)
