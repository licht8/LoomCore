"""MCP server wiring for the LoomWG plugin."""

from protocol import run
from tools import HANDLERS, TOOLS_SCHEMA


def run_server() -> None:
    run(HANDLERS, TOOLS_SCHEMA)
