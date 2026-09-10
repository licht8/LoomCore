"""WireGuard diagnostics."""
from rich.console import Console
console = Console()
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DiagnosticLevel(Enum):
    """Diagnostic result level."""

    PASS = "PASS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class DiagnosticResult:
    """Result of a diagnostic check."""

    name: str
    level: DiagnosticLevel
    message: str
    details: str | None = None


class WireGuardDiagnostics:
    """Run WireGuard diagnostics."""

    def __init__(self):
        self.config_dir = Path("/etc/wireguard")

    def check_installed(self) -> DiagnosticResult:
        """Check if WireGuard tools are installed."""
        try:
            result = subprocess.run(
                ["wg", "--version"],
                capture_output=True,
                timeout=5,
            )

            installed = result.returncode == 0

            return DiagnosticResult(
                name="WireGuard installed",
                level=DiagnosticLevel.PASS if installed else DiagnosticLevel.CRITICAL,
                message="WireGuard tools installed"
                if installed
                else "WireGuard tools not installed",
            )
        except Exception as e:
            return DiagnosticResult(
                name="WireGuard installed",
                level=DiagnosticLevel.CRITICAL,
                message="Could not check WireGuard",
                details=str(e),
            )

    def check_interface_exists(self, interface: str) -> DiagnosticResult:
        """Check if WireGuard interface exists."""
        try:
            result = subprocess.run(
                ["wg", "show", interface],
                capture_output=True,
                timeout=5,
                check=False,
            )

            exists = result.returncode == 0

            return DiagnosticResult(
                name=f"Interface {interface}",
                level=DiagnosticLevel.PASS if exists else DiagnosticLevel.WARNING,
                message=f"Interface {interface} exists"
                if exists
                else f"Interface {interface} not found",
            )
        except Exception as e:
            return DiagnosticResult(
                name=f"Interface {interface}",
                level=DiagnosticLevel.WARNING,
                message="Could not check interface",
                details=str(e),
            )

    def check_interface_active(self, interface: str) -> DiagnosticResult:
        """Check if WireGuard interface is active."""
        try:
            result = subprocess.run(
                ["wg", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            interfaces = result.stdout.split()
            is_active = interface in interfaces

            return DiagnosticResult(
                name=f"Interface {interface} active",
                level=DiagnosticLevel.PASS if is_active else DiagnosticLevel.WARNING,
                message=f"Interface {interface} is active"
                if is_active
                else f"Interface {interface} is NOT active",
            )
        except Exception as e:
            return DiagnosticResult(
                name=f"Interface {interface} active",
                level=DiagnosticLevel.WARNING,
                message="Could not check interface status",
                details=str(e),
            )

    def check_config_exists(self, interface: str) -> DiagnosticResult:
        """Check if WireGuard config exists."""
        config_file = self.config_dir / f"{interface}.conf"

        exists = config_file.exists()

        return DiagnosticResult(
            name=f"Config {interface}.conf",
            level=DiagnosticLevel.PASS if exists else DiagnosticLevel.WARNING,
            message=f"Config file exists"
            if exists
            else "Config file not found",
        )

    def check_config_permissions(self, interface: str) -> DiagnosticResult:
        """Check configuration file permissions."""
        config_file = self.config_dir / f"{interface}.conf"

        if not config_file.exists():
            return DiagnosticResult(
                name="Config permissions",
                level=DiagnosticLevel.WARNING,
                message="Config file not found",
            )

        try:
            stat = config_file.stat()
            mode = oct(stat.st_mode)[-3:]

            # Should be 600 (readable/writable by owner only)
            if mode == "600":
                return DiagnosticResult(
                    name="Config permissions",
                    level=DiagnosticLevel.PASS,
                    message=f"Correct permissions (600)",
                )

            else:
                return DiagnosticResult(
                    name="Config permissions",
                    level=DiagnosticLevel.WARNING,
                    message=f"Insecure permissions ({mode})",
                )
        except Exception as e:
            return DiagnosticResult(
                name="Config permissions",
                level=DiagnosticLevel.WARNING,
                message="Could not check permissions",
                details=str(e),
            )

    def run_all(self, interface: str | None = None) -> list[DiagnosticResult]:
        """Run all WireGuard diagnostics."""
        results = [self.check_installed()]

        if interface:
            results.extend(
                [
                    self.check_interface_exists(interface),
                    self.check_interface_active(interface),
                    self.check_config_exists(interface),
                    self.check_config_permissions(interface),
                ]
            )

        return results

    def overall_level(self, results: list[DiagnosticResult]) -> DiagnosticLevel:
        """Get overall diagnostic level."""
        if any(r.level == DiagnosticLevel.CRITICAL for r in results):
            return DiagnosticLevel.CRITICAL

        if any(r.level == DiagnosticLevel.WARNING for r in results):
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.PASS
