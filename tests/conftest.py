"""Общие фикстуры тестов LoomCore."""

import importlib
import sys
from pathlib import Path

import pytest

# Каталог плагина loomwg: модули используют bare-импорты (from wireguard import ...)
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "loomwg"
_PLUGIN_MODULES = (
    "wireguard",
    "keys",
    "config",
    "client",
    "tools",
    "lifecycle",
    "install",
    "protocol",
    "server",
)


@pytest.fixture
def load_loomwg(monkeypatch):
    """Вернуть функцию импорта модулей плагина loomwg в текущий процесс.

    Временно добавляет каталог плагина в sys.path и очищает sys.modules
    после теста, чтобы bare-импорты не затеняли другие пакеты.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))

    def _load(*names: str) -> dict:
        for name in _PLUGIN_MODULES:
            sys.modules.pop(name, None)
        return {name: importlib.import_module(name) for name in names}

    yield _load

    for name in _PLUGIN_MODULES:
        sys.modules.pop(name, None)
