"""Shared plugin manager accessor (lazy)."""

from functools import lru_cache

from src.config import settings
from src.plugins.manager import PluginManager
from src.plugins.state import resolve_state_path


@lru_cache(maxsize=1)
def get_plugin_manager() -> PluginManager:
    return PluginManager(
        state_path=resolve_state_path(settings.plugins_dir, settings.plugins_state_file)
    )


def __getattr__(name: str):
    if name == "plugin_manager":
        return get_plugin_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
