"""Network diagnostics."""
from rich.console import Console
console = Console()
import subprocess
from dataclasses import dataclass
from enum import Enum


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


class NetworkDiagnostics:
    """Run network diagnostics."""

    def __init__(self):
        pass

    def check_default_route(self) -> DiagnosticResult:
        """Check if default route exists."""
        try:
            result = subprocess.run(
                ["ip", "route", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return DiagnosticResult(
                    name="Default route",
                    level=DiagnosticLevel.CRITICAL,
                    message="Could not check routes",
                )

            has_default = any(
                line.startswith("default") for line in result.stdout.splitlines()
            )

            return DiagnosticResult(
                name="Default route",
                level=DiagnosticLevel.PASS if has_default else DiagnosticLevel.CRITICAL,
                message="Default route configured"
                if has_default
                else "No default route",
            )
        except Exception as e:
            return DiagnosticResult(
                name="Default route",
                level=DiagnosticLevel.WARNING,
                message="Could not check default route",
                details=str(e),
            )

    def check_dns_resolution(self) -> DiagnosticResult:
        """Check DNS resolution."""
        try:
            result = subprocess.run(
                ["nslookup", "google.com"],
                capture_output=True,
                timeout=5,
            )

            success = result.returncode == 0

            return DiagnosticResult(
                name="DNS resolution",
                level=DiagnosticLevel.PASS if success else DiagnosticLevel.WARNING,
                message="DNS working" if success else "DNS resolution failed",
            )
        except Exception as e:
            return DiagnosticResult(
                name="DNS resolution",
                level=DiagnosticLevel.WARNING,
                message="Could not check DNS",
                details=str(e),
            )

    def check_interface_up(self, interface: str) -> DiagnosticResult:
        """Check if interface is up."""
        try:
            result = subprocess.run(
                ["ip", "link", "show", interface],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return DiagnosticResult(
                    name=f"Interface {interface}",
                    level=DiagnosticLevel.WARNING,
                    message="Interface not found",
                )

            is_up = "UP" in result.stdout

            return DiagnosticResult(
                name=f"Interface {interface}",
                level=DiagnosticLevel.PASS if is_up else DiagnosticLevel.WARNING,
                message="Interface UP" if is_up else "Interface DOWN",
            )
        except Exception as e:
            return DiagnosticResult(
                name=f"Interface {interface}",
                level=DiagnosticLevel.WARNING,
                message="Could not check interface",
                details=str(e),
            )

    def run_all(self, interface: str | None = None) -> list[DiagnosticResult]:
        """Run all network diagnostics."""
        results = [
            self.check_default_route(),
            self.check_dns_resolution(),
        ]

        if interface:
            results.append(self.check_interface_up(interface))

        return results

    def overall_level(self, results: list[DiagnosticResult]) -> DiagnosticLevel:
        """Get overall diagnostic level."""
        if any(r.level == DiagnosticLevel.CRITICAL for r in results):
            return DiagnosticLevel.CRITICAL

        if any(r.level == DiagnosticLevel.WARNING for r in results):
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.PASS
