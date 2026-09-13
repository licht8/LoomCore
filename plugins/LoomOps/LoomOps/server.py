"""MCP server wiring for the LoomOps plugin."""

import logging

from . import __version__
from .ops import commands, files
from .protocol import run
from .tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("LoomOps.server")


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
    _log_startup()
    run(HANDLERS, TOOLS_SCHEMA)
