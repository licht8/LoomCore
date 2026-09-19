"""MCP server wiring for the LoomOps plugin."""

import logging
import os
import sys

from . import __version__
from .ops import commands, files
from .protocol import run
from .tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("LoomOps.server")


class _PluginStderrHandler(logging.StreamHandler):
    """Stderr handler owned by this plugin, so reloads replace only this one."""


def _configure_logging() -> None:
    """Send plugin logs to stderr; the core drains stderr into its own logs."""
    handler = _PluginStderrHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    # Configure the plugin's own logger: basicConfig() is a no-op when the host
    # process already configured the root logger, which would silently ignore
    # both the stderr stream and LOOMOPS_LOG_LEVEL.
    plugin_logger = logging.getLogger("LoomOps")
    # Replace only the handler this function owns (matched by type, not by
    # stream identity), so a reload stays idempotent even if sys.stderr was
    # swapped and any handler a host/embedder attached is preserved.
    for existing in list(plugin_logger.handlers):
        if isinstance(existing, _PluginStderrHandler):
            plugin_logger.removeHandler(existing)
            existing.close()
    plugin_logger.addHandler(handler)
    plugin_logger.propagate = False

    raw_level = os.environ.get("LOOMOPS_LOG_LEVEL", "INFO").strip()
    level = getattr(logging, raw_level.upper(), None)
    # NOTSET (0) is a valid attribute but not a usable level: it would leave the
    # logger inheriting the root logger's level.
    unknown = not isinstance(level, int) or level == logging.NOTSET
    if unknown:
        level = logging.INFO
    # Set the level before emitting the warning: the logger may already carry a
    # higher level from an earlier (or host) configuration, which would filter
    # the warning out.
    plugin_logger.setLevel(level)
    if unknown:
        plugin_logger.warning("Unknown LOOMOPS_LOG_LEVEL %r, using INFO", raw_level)


def _log_startup() -> None:
    allowed = sorted(commands.allowed_commands())
    allowed_paths = [str(path) for path in files.allowed_paths()]
    logger.info(
        "LoomOps %s starting; allowed commands: %s; allowed paths: %s",
        __version__,
        ", ".join(allowed) if allowed else "(none)",
        ", ".join(allowed_paths) if allowed_paths else "(none)",
    )


def run_server() -> None:
    _configure_logging()
    _log_startup()
    run(HANDLERS, TOOLS_SCHEMA)
