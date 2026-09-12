"""Shared fixtures for the LoomCore test suite."""

import importlib
import sys
from pathlib import Path

import pytest

# loomwg plugin directory: its modules use bare imports (from wireguard import ...)
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "loomwg"
_PLUGIN_MODULES = (
    "wireguard",
    "keys",
    "config",
    "client",
    "clients_store",
    "tools",
    "lifecycle",
    "install",
    "protocol",
    "server",
    "results",
    "validate",
    "config_view",
    "service_mode",
    "stats",
)


@pytest.fixture
def load_loomwg(monkeypatch):
    """Return a loader that imports loomwg plugin modules into the current process.

    Temporarily prepends the plugin directory to sys.path and clears sys.modules
    after the test so bare imports do not shadow other packages.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))

    def _load(*names: str) -> dict:
        for name in _PLUGIN_MODULES:
            sys.modules.pop(name, None)
        return {name: importlib.import_module(name) for name in names}

    yield _load

    for name in _PLUGIN_MODULES:
        sys.modules.pop(name, None)
