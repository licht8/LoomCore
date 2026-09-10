import logging
"""Network operations and utilities."""
import subprocess
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from pathlib import Path


@dataclass
class NetworkInterface:
    """Information about a network interface."""

    name: str
    ip_address: str | None
    gateway: str | None
    is_up: bool


logger = logging.getLogger(__name__)


class NetworkManager:
    """Manage network configuration."""

    def __init__(self):
        pass

    def get_interfaces(self) -> list[str]:
        """Get list of network interfaces."""
        try:
            result = subprocess.run(
                ["ip", "link", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return []

            interfaces = []
            for line in result.stdout.splitlines():
                if ":" in line and not line.startswith(" "):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        iface = parts[1].strip()
                        if iface:
                            interfaces.append(iface)

            return interfaces
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return []

    def get_default_interface(self) -> str | None:
        """Get default network interface."""
        try:
            result = subprocess.run(
                ["ip", "route", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return None

            for line in result.stdout.splitlines():
                if line.startswith("default"):
                    parts = line.split()
                    if len(parts) >= 5:
                        return parts[4]

            return None
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return None

    def get_ipv4_address(self, interface: str) -> str | None:
        """Get IPv4 address for an interface."""
        try:
            result = subprocess.run(
                ["ip", "addr", "show", interface],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return None

            for line in result.stdout.splitlines():
                if "inet " in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        return parts[1].split("/")[0]

            return None
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return None

    def enable_ip_forwarding(self) -> bool:
        """Enable IPv4 forwarding."""
        try:
            result = subprocess.run(
                ["sysctl", "-w", "net.ipv4.ip_forward=1"],
                capture_output=True,
                timeout=5,
            )

            return result.returncode == 0
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return False

    def enable_ipv6_forwarding(self) -> bool:
        """Enable IPv6 forwarding."""
        try:
            result = subprocess.run(
                ["sysctl", "-w", "net.ipv6.conf.all.forwarding=1"],
                capture_output=True,
                timeout=5,
            )

            return result.returncode == 0
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return False

    def is_ipv4_forwarding_enabled(self) -> bool:
        """Check if IPv4 forwarding is enabled."""
        try:
            result = subprocess.run(
                ["sysctl", "-n", "net.ipv4.ip_forward"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                return result.stdout.strip() == "1"

            return False
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return False

    def is_ipv6_forwarding_enabled(self) -> bool:
        """Check if IPv6 forwarding is enabled."""
        try:
            result = subprocess.run(
                ["sysctl", "-n", "net.ipv6.conf.all.forwarding"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                return result.stdout.strip() == "1"

            return False
        except Exception as exc:
            logger.error("Network operation failed: %s", exc)
            return False
