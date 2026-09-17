"""Shared fixtures for the LoomCore test suite."""

import importlib
import sys
from pathlib import Path

import pytest

# LoomWG plugin directory: `import LoomWG` resolves to plugins/LoomWG/LoomWG.
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "LoomWG"
# LoomOps plugin directory: `import LoomOps` resolves to plugins/LoomOps/LoomOps.
LOOMOPS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "LoomOps"
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
    "dependencies": "LoomWG.ops.dependencies",
    "provision": "LoomWG.ops.provision",
    "lifecycle": "LoomWG.ops.lifecycle",
    "service_mode": "LoomWG.ops.service",
    "stats": "LoomWG.ops.stats",
    "tools": "LoomWG.tools",
    "schemas": "LoomWG.schemas",
    "protocol": "LoomWG.protocol",
    "server": "LoomWG.server",
    "results": "LoomWG.results",
    "manifest": "LoomWG.manifest",
}

# Logical module names used by LoomOps tests -> dotted paths inside the package.
_LOOMOPS_MODULE_PATHS = {
    "ops": "LoomOps.ops",
    "commands": "LoomOps.ops.commands",
    "files": "LoomOps.ops.files",
    "processes": "LoomOps.ops.processes",
    "services": "LoomOps.ops.services",
    "system_info": "LoomOps.ops.system_info",
    "monitoring": "LoomOps.ops.monitoring",
    "logs": "LoomOps.ops.logs",
    "diagnostics": "LoomOps.ops.diagnostics",
    "tools": "LoomOps.tools",
    "schemas": "LoomOps.schemas",
    "protocol": "LoomOps.protocol",
    "server": "LoomOps.server",
    "results": "LoomOps.results",
}


def _purge_package(name: str = "LoomWG") -> None:
    for module_name in list(sys.modules):
        if module_name == name or module_name.startswith(f"{name}."):
            sys.modules.pop(module_name, None)


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


@pytest.fixture
def load_loomops(monkeypatch):
    """Return a loader that imports LoomOps plugin modules into the current process."""
    monkeypatch.syspath_prepend(str(LOOMOPS_DIR))

    def _load(*names: str) -> dict:
        _purge_package("LoomOps")
        return {name: importlib.import_module(_LOOMOPS_MODULE_PATHS[name]) for name in names}

    yield _load

    _purge_package("LoomOps")
