"""MCP server wiring for the LoomWG plugin."""

import logging

from .clients.client import migrate_client_layout
from .ops.service import apply_autostart
from .protocol import run
from .tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("LoomWG.server")


def run_server() -> None:
    try:
        apply_autostart()
    except Exception as exc:  # autostart must never block plugin startup
        logger.warning("autostart failed: %s", exc)
    try:
        migrate_client_layout()
    except Exception as exc:  # migration must never block plugin startup
        logger.warning("client layout migration failed: %s", exc)
    run(HANDLERS, TOOLS_SCHEMA)
