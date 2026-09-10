"""WireGuard management module."""
from .config_generator import ConfigGenerator
from .installer import WireGuardInstaller
from .ip_allocator import IPAllocator
from .key_manager import KeyManager
from .manager import WireGuardManager
from .peer_manager import Peer, PeerManager
from .server_config import ServerConfig
from .status import StatusParser
from .validation import AllowedIPValidationManager
from .interfaces import (
    configured_interfaces,
    get_selected_interface,
    set_selected_interface,
    validate_interface_name,
)

__all__ = [
    "WireGuardManager",
    "WireGuardInstaller",
    "KeyManager",
    "ConfigGenerator",
    "ServerConfig",
    "PeerManager",
    "Peer",
    "IPAllocator",
    "StatusParser",
    "AllowedIPValidationManager",
    "configured_interfaces",
    "get_selected_interface",
    "set_selected_interface",
    "validate_interface_name",
]
