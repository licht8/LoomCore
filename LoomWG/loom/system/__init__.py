"""System management module."""
from .command import CommandResult, CommandRunner
from .info import SystemDetector, SystemInfo
from .network import NetworkManager
from .packages import PackageManager
from .services import ServiceManager

__all__ = [
    "SystemDetector",
    "SystemInfo",
    "CommandRunner",
    "CommandResult",
    "PackageManager",
    "ServiceManager",
    "NetworkManager",
]
