"""Shared fixtures for the LoomCore test suite."""

import importlib
import sys
from pathlib import Path

import pytest

# loomwg plugin directory: `import loomwg` resolves to plugins/loomwg/loomwg.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "loomwg"
# Logical module names used by tests -> dotted paths inside the package.
_MODULE_PATHS = {
    "wireguard": "loomwg.wg.device",
    "keys": "loomwg.wg.keys",
    "validate": "loomwg.wg.validate",
    "config": "loomwg.peers.config",
    "config_view": "loomwg.peers.view",
    "clients_store": "loomwg.clients.store",
    "client": "loomwg.clients.client",
    "install": "loomwg.ops.install",
    "lifecycle": "loomwg.ops.lifecycle",
    "service_mode": "loomwg.ops.service",
    "stats": "loomwg.ops.stats",
    "tools": "loomwg.tools",
    "protocol": "loomwg.protocol",
    "server": "loomwg.server",
    "results": "loomwg.results",
}


def _purge_package() -> None:
    for name in list(sys.modules):
        if name == "loomwg" or name.startswith("loomwg."):
            sys.modules.pop(name, None)


@pytest.fixture
def load_loomwg(monkeypatch):
    """Return a loader that imports loomwg plugin modules into the current process.

    Prepends the plugin directory to sys.path so `import loomwg` resolves, and
    purges the package between loads so module-level state (env-derived paths)
    is recomputed.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))

    def _load(*names: str) -> dict:
        _purge_package()
        return {name: importlib.import_module(_MODULE_PATHS[name]) for name in names}

    yield _load

    _purge_package()
