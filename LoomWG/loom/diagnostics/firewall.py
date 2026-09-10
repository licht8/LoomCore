"""Firewall diagnostics."""
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


class FirewallDiagnostics:
    """Run firewall diagnostics."""

    def __init__(self):
        from ..firewall import FirewalldManager

        self.firewall = FirewalldManager()

    def check_firewalld_installed(self) -> DiagnosticResult:
        """Check if firewalld is installed."""
        installed = self.firewall.is_installed()

        return DiagnosticResult(
            name="firewalld installed",
            level=DiagnosticLevel.PASS if installed else DiagnosticLevel.WARNING,
            message="firewalld installed"
            if installed
            else "firewalld not installed",
        )

    def check_firewalld_running(self) -> DiagnosticResult:
        """Check if firewalld is running."""
        if not self.firewall.is_installed():
            return DiagnosticResult(
                name="firewalld running",
                level=DiagnosticLevel.WARNING,
                message="firewalld not installed",
            )

        running = self.firewall.is_running()

        return DiagnosticResult(
            name="firewalld running",
            level=DiagnosticLevel.PASS if running else DiagnosticLevel.WARNING,
            message="firewalld running"
            if running
            else "firewalld not running",
        )

    def check_firewalld_enabled(self) -> DiagnosticResult:
        """Check if firewalld is enabled on boot."""
        if not self.firewall.is_installed():
            return DiagnosticResult(
                name="firewalld enabled",
                level=DiagnosticLevel.WARNING,
                message="firewalld not installed",
            )

        enabled = self.firewall.is_enabled()

        return DiagnosticResult(
            name="firewalld enabled",
            level=DiagnosticLevel.PASS if enabled else DiagnosticLevel.WARNING,
            message="firewalld enabled on boot"
            if enabled
            else "firewalld not enabled on boot",
        )

    def check_port_open(self, port: int, protocol: str = "udp") -> DiagnosticResult:
        """Check if a port is open in firewall."""
        if not self.firewall.is_running():
            return DiagnosticResult(
                name=f"Port {port} open",
                level=DiagnosticLevel.WARNING,
                message="firewalld not running",
            )

        is_open = self.firewall.is_port_open(port, protocol)

        return DiagnosticResult(
            name=f"Port {port}/{protocol} open",
            level=DiagnosticLevel.PASS if is_open else DiagnosticLevel.WARNING,
            message=f"Port {port}/{protocol} open"
            if is_open
            else f"Port {port}/{protocol} not open",
        )

    def check_masquerading(self) -> DiagnosticResult:
        """Check if masquerading is enabled."""
        if not self.firewall.is_running():
            return DiagnosticResult(
                name="Masquerading",
                level=DiagnosticLevel.WARNING,
                message="firewalld not running",
            )

        enabled = self.firewall.is_masquerading_enabled()

        return DiagnosticResult(
            name="Masquerading",
            level=DiagnosticLevel.PASS if enabled else DiagnosticLevel.WARNING,
            message="Masquerading enabled"
            if enabled
            else "Masquerading not enabled",
        )

    def run_all(self, port: int | None = None) -> list[DiagnosticResult]:
        """Run all firewall diagnostics."""
        results = [
            self.check_firewalld_installed(),
            self.check_firewalld_running(),
            self.check_firewalld_enabled(),
        ]

        if port:
            results.append(self.check_port_open(port))

        results.append(self.check_masquerading())

        return results

    def overall_level(self, results: list[DiagnosticResult]) -> DiagnosticLevel:
        """Get overall diagnostic level."""
        if any(r.level == DiagnosticLevel.CRITICAL for r in results):
            return DiagnosticLevel.CRITICAL

        if any(r.level == DiagnosticLevel.WARNING for r in results):
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.PASS
