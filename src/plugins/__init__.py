"""Shared plugin manager accessor (lazy)."""

import threading

from src.config import settings
from src.plugins.manager import PluginManager
from src.plugins.state import resolve_state_path

_lock = threading.Lock()
_instance: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """Return the process-wide plugin manager, constructing it once."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = PluginManager(
                    state_path=resolve_state_path(
                        settings.plugins_dir, settings.plugins_state_file
                    )
                )
    return _instance


def cache_clear() -> None:
    """Drop the cached manager so the next call re-reads settings."""
    global _instance
    with _lock:
        _instance = None


get_plugin_manager.cache_clear = cache_clear


def __getattr__(name: str):
    if name == "plugin_manager":
        return get_plugin_manager()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
