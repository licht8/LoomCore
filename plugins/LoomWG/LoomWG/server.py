"""MCP server wiring for the LoomWG plugin."""

import logging

from .clients.client import migrate_client_layout
from .manifest import runtime_report
from .ops.service import apply_autostart
from .protocol import run
from .tools import HANDLERS, TOOLS_SCHEMA

logger = logging.getLogger("LoomWG.server")


def _log_manifest_report() -> None:
    report = runtime_report()
    for issue in report["issues"]:
        logger.warning("manifest issue: %s", issue)
    for warning in report["permission_warnings"]:
        logger.warning("permission check: %s", warning)
    for note in report["permission_notes"]:
        logger.info("optional permission: %s", note)


def run_server() -> None:
    _log_manifest_report()
    try:
        apply_autostart()
    except Exception as exc:  # autostart must never block plugin startup
        logger.warning("autostart failed: %s", exc)
    try:
        migrate_client_layout()
    except Exception as exc:  # migration must never block plugin startup
        logger.warning("client layout migration failed: %s", exc)
    run(HANDLERS, TOOLS_SCHEMA)
