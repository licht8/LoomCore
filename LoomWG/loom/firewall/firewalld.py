"""Firewalld integration for WireGuard."""
import subprocess
from rich.console import Console
from dataclasses import dataclass


@dataclass
class FirewallStatus:
    """Status of firewall configuration."""

    is_running: bool
    is_enabled: bool
    udp_port_open: bool | None = None
    masquerading_enabled: bool | None = None
    forwarding_enabled: bool | None = None


console = Console()


class FirewalldManager:
    """Manage firewalld configuration."""

    # Protocols accepted by firewalld --add-port/--query-port (firewalld.rich(5)).
    _VALID_PROTOCOLS = frozenset({"udp", "tcp", "sctp", "dccp"})

    def __init__(self):
        self.service_name = "firewalld"

    def is_installed(self) -> bool:
        """Check if firewalld is installed."""
        try:
            result = subprocess.run(
                ["which", "firewall-cmd"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def is_running(self) -> bool:
        """Check if firewalld is running."""
        try:
            result = subprocess.run(
                ["firewall-cmd", "--state"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def is_enabled(self) -> bool:
        """Check if firewalld is enabled on boot."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", self.service_name],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def start(self) -> bool:
        """Start firewalld."""
        try:
            result = subprocess.run(
                ["systemctl", "start", self.service_name],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Error starting firewalld: {exc.stderr}[/]")
            return False
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def enable(self) -> bool:
        """Enable firewalld on boot."""
        try:
            result = subprocess.run(
                ["systemctl", "enable", self.service_name],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Error enabling firewalld: {exc.stderr}[/]")
            return False
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def _validate_protocol(self, protocol: str) -> None:
        """Validate a firewall zone protocol identifier."""
        if protocol not in self._VALID_PROTOCOLS:
            raise ValueError(f"Unsupported protocol: {protocol}")

    def open_port(self, port: int, protocol: str = "udp") -> bool:
        """Open a port in firewall."""
        self._validate_protocol(protocol)
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--add-port",
                    f"{port}/{protocol}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            if result.returncode != 0:
                return False

            # Reload firewall
            return self.reload()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def close_port(self, port: int, protocol: str = "udp") -> bool:
        """Close a port in firewall."""
        self._validate_protocol(protocol)
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--remove-port",
                    f"{port}/{protocol}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            if result.returncode != 0:
                return False

            # Reload firewall
            return self.reload()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def is_port_open(self, port: int, protocol: str = "udp") -> bool:
        """Check if a port is open."""
        self._validate_protocol(protocol)
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--query-port",
                    f"{port}/{protocol}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def enable_masquerading(self, zone: str = "public") -> bool:
        """Enable masquerading (IPv4 NAT)."""
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--zone",
                    zone,
                    "--add-masquerade",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            if result.returncode != 0:
                return False

            return self.reload()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def disable_masquerading(self, zone: str = "public") -> bool:
        """Disable masquerading."""
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--permanent",
                    "--zone",
                    zone,
                    "--remove-masquerade",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            if result.returncode != 0:
                return False

            return self.reload()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def is_masquerading_enabled(self, zone: str = "public") -> bool:
        """Check if masquerading is enabled."""
        try:
            result = subprocess.run(
                [
                    "firewall-cmd",
                    "--zone",
                    zone,
                    "--query-masquerade",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def reload(self) -> bool:
        """Reload firewall configuration."""
        try:
            result = subprocess.run(
                ["firewall-cmd", "--reload"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Error reloading firewall: {exc.stderr}[/]")
            return False
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def get_status(self) -> FirewallStatus:
        """Get firewall status."""
        return FirewallStatus(
            is_running=self.is_running(),
            is_enabled=self.is_enabled(),
        )