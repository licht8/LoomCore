"""System diagnostics."""
from rich.console import Console
console = Console()
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


class SystemDiagnostics:
    """Run system diagnostics."""

    def __init__(self):
        pass

    def check_root(self) -> DiagnosticResult:
        """Check if running as root."""
        import os

        is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False

        return DiagnosticResult(
            name="Root privileges",
            level=DiagnosticLevel.PASS if is_root else DiagnosticLevel.CRITICAL,
            message="Running as root" if is_root else "NOT running as root",
        )

    def check_disk_space(self) -> DiagnosticResult:
        """Check available disk space."""
        import shutil

        try:
            stat = shutil.disk_usage("/etc/wireguard")
            free_gb = stat.free / (1024 ** 3)

            if free_gb > 1:
                return DiagnosticResult(
                    name="Disk space",
                    level=DiagnosticLevel.PASS,
                    message=f"{free_gb:.1f} GB available",
                )

            elif free_gb > 0.1:
                return DiagnosticResult(
                    name="Disk space",
                    level=DiagnosticLevel.WARNING,
                    message=f"Low disk space: {free_gb:.1f} GB",
                )

            else:
                return DiagnosticResult(
                    name="Disk space",
                    level=DiagnosticLevel.CRITICAL,
                    message=f"Critical disk space: {free_gb:.1f} GB",
                )
        except Exception as e:
            return DiagnosticResult(
                name="Disk space",
                level=DiagnosticLevel.WARNING,
                message="Could not check disk space",
                details=str(e),
            )

    def check_memory(self) -> DiagnosticResult:
        """Check available memory."""
        try:
            import psutil  # type: ignore

            mem = psutil.virtual_memory()

            if mem.percent < 80:
                return DiagnosticResult(
                    name="Memory",
                    level=DiagnosticLevel.PASS,
                    message=f"{mem.percent:.1f}% used",
                )

            elif mem.percent < 90:
                return DiagnosticResult(
                    name="Memory",
                    level=DiagnosticLevel.WARNING,
                    message=f"High memory usage: {mem.percent:.1f}%",
                )

            else:
                return DiagnosticResult(
                    name="Memory",
                    level=DiagnosticLevel.CRITICAL,
                    message=f"Critical memory usage: {mem.percent:.1f}%",
                )
        except ImportError:
            return DiagnosticResult(
                name="Memory",
                level=DiagnosticLevel.WARNING,
                message="psutil not available",
            )

    def run_all(self) -> list[DiagnosticResult]:
        """Run all system diagnostics."""
        return [
            self.check_root(),
            self.check_disk_space(),
            self.check_memory(),
        ]

    def overall_level(self, results: list[DiagnosticResult]) -> DiagnosticLevel:
        """Get overall diagnostic level."""
        if any(r.level == DiagnosticLevel.CRITICAL for r in results):
            return DiagnosticLevel.CRITICAL

        if any(r.level == DiagnosticLevel.WARNING for r in results):
            return DiagnosticLevel.WARNING

        return DiagnosticLevel.PASS
