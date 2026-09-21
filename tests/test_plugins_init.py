"""Unit tests for the lazy shared plugin manager accessor."""

import pytest

from src import plugins as plugins_pkg
from src.plugins import get_plugin_manager, plugin_manager
from src.plugins.manager import PluginManager
from src.plugins.state import resolve_state_path


def test_get_plugin_manager_returns_a_cached_plugin_manager():
    manager = get_plugin_manager()
    assert isinstance(manager, PluginManager)
    assert get_plugin_manager() is manager


def test_plugin_manager_attribute_resolves_through_the_factory():
    assert plugin_manager is get_plugin_manager()


def test_manager_state_path_follows_settings(monkeypatch, tmp_path):
    plugins_dir = str(tmp_path / "plugins")
    get_plugin_manager.cache_clear()
    try:
        monkeypatch.setattr("src.plugins.settings.plugins_dir", plugins_dir)
        monkeypatch.setattr("src.plugins.settings.plugins_state_file", "")
        manager = get_plugin_manager()
        assert manager._state_path == resolve_state_path(plugins_dir, "")
    finally:
        get_plugin_manager.cache_clear()


def test_unknown_attribute_raises_attribute_error():
    name = "definitely_not_a_manager"
    with pytest.raises(AttributeError):
        getattr(plugins_pkg, name)
