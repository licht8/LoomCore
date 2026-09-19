"""MCP server wiring for the LoomOps plugin."""

import logging
import os
import sys

from . import __version__
from .ops import commands, files
from .protocol import run
from .tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("LoomOps.server")


def _configure_logging() -> None:
    """Send plugin logs to stderr; the core drains stderr into its own logs."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    # Configure the plugin's own logger: basicConfig() is a no-op when the host
    # process already configured the root logger, which would silently ignore
    # both the stderr stream and LOOMOPS_LOG_LEVEL.
    plugin_logger = logging.getLogger("LoomOps")
    plugin_logger.handlers = [handler]
    plugin_logger.propagate = False

    level_name = os.environ.get("LOOMOPS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    unknown = not isinstance(level, int)
    if unknown:
        level = logging.INFO
    # Set the level before emitting the warning: at NOTSET, isEnabledFor walks
    # up to the root logger, so a host configured above WARNING would drop it.
    plugin_logger.setLevel(level)
    if unknown:
        plugin_logger.warning("Unknown LOOMOPS_LOG_LEVEL %r, using INFO", level_name)


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
