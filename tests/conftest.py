"""Shared fixtures for the LoomCore test suite."""

import importlib
import sys
from pathlib import Path

import pytest

# LoomWG plugin directory: `import LoomWG` resolves to plugins/LoomWG/LoomWG.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "LoomWG"
# Logical module names used by tests -> dotted paths inside the package.
_MODULE_PATHS = {
    "wireguard": "LoomWG.wg.device",
    "keys": "LoomWG.wg.keys",
    "validate": "LoomWG.wg.validate",
    "config": "LoomWG.peers.config",
    "config_view": "LoomWG.peers.view",
    "clients_store": "LoomWG.clients.store",
    "client": "LoomWG.clients.client",
    "install": "LoomWG.ops.install",
    "lifecycle": "LoomWG.ops.lifecycle",
    "service_mode": "LoomWG.ops.service",
    "stats": "LoomWG.ops.stats",
    "tools": "LoomWG.tools",
    "protocol": "LoomWG.protocol",
    "server": "LoomWG.server",
    "results": "LoomWG.results",
    "manifest": "LoomWG.manifest",
}


def _purge_package() -> None:
    for name in list(sys.modules):
        if name == "LoomWG" or name.startswith("LoomWG."):
            sys.modules.pop(name, None)


@pytest.fixture
def load_loomwg(monkeypatch):
    """Return a loader that imports LoomWG plugin modules into the current process.

    Prepends the plugin directory to sys.path so `import LoomWG` resolves, and
    purges the package between loads so module-level state (env-derived paths)
    is recomputed.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))

    def _load(*names: str) -> dict:
        _purge_package()
        return {name: importlib.import_module(_MODULE_PATHS[name]) for name in names}

    yield _load

    _purge_package()
