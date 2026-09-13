from pathlib import Path

from src.config import settings
from src.plugins.manager import PluginManager
from src.plugins.state import resolve_state_path

# Shared singleton — a single instance for the whole application
plugin_manager = PluginManager(
    state_path=Path(resolve_state_path(settings.plugins_dir, settings.plugins_state_file))
)
