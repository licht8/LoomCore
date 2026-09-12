"""MCP server wiring for the LoomWG plugin."""

import logging

from client import migrate_client_layout
from protocol import run
from service_mode import apply_autostart
from tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("loomwg.server")


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
