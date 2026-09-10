"""Safe WireGuard removal and reinstallation helpers."""
from dataclasses import dataclass
from pathlib import Path

from ..firewall.firewalld import FirewalldManager
from ..system.command import CommandRunner
from ..system.packages import PackageManager
from .manager import WireGuardManager


@dataclass
class LifecycleResult:
    success: bool
    message: str


class WireGuardLifecycle:
    """Remove LoomWG-managed WireGuard state, or prepare a clean reinstall."""

    CONFIG_DIR = Path("/etc/wireguard")
    SYSCTL_FILE = Path("/etc/sysctl.d/99-loomwg.conf")
    PACKAGES = ("wireguard-tools", "qrencode")

    def __init__(self, runner: CommandRunner | None = None):
        self.runner = runner or CommandRunner()
        self.wireguard = WireGuardManager(self.runner)
        self.packages = PackageManager(self.runner)
        self.firewall = FirewalldManager()

    def remove(self, interface: str = "wg0") -> LifecycleResult:
        """Remove the managed interface, service, firewall rule, and packages."""
        config = self.wireguard.read_config(interface)
        port = self._listen_port(config) if config else None
        self.runner.run(["systemctl", "disable", "--now", f"wg-quick@{interface}"], timeout=30)
        if self.wireguard.is_interface_active(interface):
            self.wireguard.stop(interface)
        if port and self.firewall.is_running():
            self.firewall.close_port(port)
            self.firewall.disable_masquerading()
        # Only LoomWG's forwarding file is removed; unrelated system settings remain intact.
        try:
            if self.SYSCTL_FILE.exists():
                self.SYSCTL_FILE.unlink()
                self.runner.run(["sysctl", "--system"], timeout=30)
        except OSError as exc:
            return LifecycleResult(False, f"Could not remove forwarding settings: {exc}")
        try:
            if self.CONFIG_DIR.exists():
                for path in self.CONFIG_DIR.glob("*.conf"):
                    path.unlink()
                self.CONFIG_DIR.rmdir()
        except OSError as exc:
            return LifecycleResult(False, f"Could not remove WireGuard configuration: {exc}")
        installed = [p for p in self.PACKAGES if self.packages.is_installed(p)]
        if installed and not self.packages.remove(installed):
            return LifecycleResult(False, "Interface removed, but package removal failed.")
        return LifecycleResult(True, "WireGuard, LoomWG configuration, service, and managed firewall settings removed.")

    @staticmethod
    def _listen_port(config: str) -> int | None:
        for line in config.splitlines():
            if line.strip().startswith("ListenPort") and "=" in line:
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    return None
        return None
