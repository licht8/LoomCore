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
    "files": "LoomWG.util.files",
    "config": "LoomWG.peers.config",
    "config_view": "LoomWG.peers.view",
    "clients_store": "LoomWG.clients.store",
    "client": "LoomWG.clients.client",
    "qr": "LoomWG.clients.qr",
    "render": "LoomWG.clients.render",
    "install": "LoomWG.ops.install",
    "dependencies": "LoomWG.ops.dependencies",
    "provision": "LoomWG.ops.provision",
    "shell": "LoomWG.ops.shell",
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
# Every key is namespaced with the plugin id so the two maps stay disjoint.
_LOOMOPS_MODULE_PATHS = {
    "loomops.ops": "LoomOps.ops",
    "loomops.commands": "LoomOps.ops.commands",
    "loomops.files": "LoomOps.ops.files",
    "loomops.processes": "LoomOps.ops.processes",
    "loomops.services": "LoomOps.ops.services",
    "loomops.system_info": "LoomOps.ops.system_info",
    "loomops.monitoring": "LoomOps.ops.monitoring",
    "loomops.logs": "LoomOps.ops.logs",
    "loomops.diagnostics": "LoomOps.ops.diagnostics",
    "loomops.tools": "LoomOps.tools",
    "loomops.schemas": "LoomOps.schemas",
    "loomops.protocol": "LoomOps.protocol",
    "loomops.server": "LoomOps.server",
    "loomops.results": "LoomOps.results",
}


def _purge_package(name: str = "LoomWG") -> None:
    for module_name in list(sys.modules):
        if module_name == name or module_name.startswith(f"{name}."):
            sys.modules.pop(module_name, None)


def _make_loader(package: str, paths: dict[str, str]):
    """Build a loader that imports ``package`` modules and caches them.

    The package is purged once before the first load and once at teardown, so
    module-level state (env-derived paths) is recomputed per test while repeated
    calls inside one test keep module identity.
    """
    loaded: dict = {}

    def _load(*names: str) -> dict:
        for name in names:
            if name not in loaded:
                module = importlib.import_module(paths[name])
                root = module.__name__.split(".", 1)[0]
                assert root == package, (
                    f"logical name {name!r} resolved to {module.__name__!r}, "
                    f"which does not belong to the {package} loader"
                )
                loaded[name] = module
        return {name: loaded[name] for name in names}

    return _load


@pytest.fixture
def load_loomwg(monkeypatch):
    """Return a loader that imports LoomWG plugin modules into the current process.

    Prepends the plugin directory to sys.path so `import LoomWG` resolves, and
    purges the package once per fixture so module-level state (env-derived paths)
    is recomputed while module identity survives repeated loads within a test.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    _purge_package()

    yield _make_loader("LoomWG", _MODULE_PATHS)

    _purge_package()


@pytest.fixture
def load_loomops(monkeypatch):
    """Return a loader that imports LoomOps plugin modules into the current process."""
    monkeypatch.syspath_prepend(str(LOOMOPS_DIR))
    _purge_package("LoomOps")

    yield _make_loader("LoomOps", _LOOMOPS_MODULE_PATHS)

    _purge_package("LoomOps")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear slowapi state before every test so limits never leak between them."""
    from src.core.rate_limit import limiter

    limiter.reset()
    yield
