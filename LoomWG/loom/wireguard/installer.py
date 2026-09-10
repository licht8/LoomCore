from dataclasses import dataclass
from pathlib import Path

from ..system.command import CommandRunner
from ..system.info import SystemDetector
from ..system.packages import PackageManager
from .manager import WireGuardManager


@dataclass
class InstallResult:
    """Result of WireGuard installation."""

    success: bool
    message: str
    details: str = ""


class WireGuardInstaller:
    """Install and configure WireGuard on Rocky Linux."""

    REQUIRED_PACKAGES = [
        "wireguard-tools",
        "qrencode",
        "firewalld",
    ]

    ROCKY_EXTRA_PACKAGES = [
        "elrepo-release",
    ]

    ROCKY_KERNEL_MODULE_CANDIDATES = [
        "kmod-wireguard",
        "wireguard-dkms",
        "wireguard-kmod",
        "wireguard",
    ]

    def __init__(
        self,
        runner: CommandRunner | None = None,
        detector: SystemDetector | None = None,
        package_manager: PackageManager | None = None,
        wireguard: WireGuardManager | None = None,
        interface_name: str = "wg0",
    ):
        self.runner = runner or CommandRunner()
        self.detector = detector or SystemDetector()
        self.package_manager = package_manager or PackageManager(
            self.runner
        )
        self.wireguard = wireguard or WireGuardManager(
            self.runner
        )

        self.interface_name = interface_name
        self.config_dir = Path("/etc/wireguard")

    def install(self, interface: str | None = None) -> InstallResult:
        """Run the complete WireGuard installation for a chosen interface."""
        if interface:
            self.interface_name = interface

        checks = self.detector.check()

        failed_checks = [
            check for check in checks
            if not check.passed and check.name not in {"firewalld"}
        ]

        if failed_checks:
            messages = "\n".join(
                f"- {check.name}: {check.message}"
                for check in failed_checks
            )

            return InstallResult(
                success=False,
                message=(
                    "System requirements are not satisfied:\n"
                    f"{messages}"
                ),
                details=messages,
            )

        self._warn_on_low_resources()
        should_recommend_swap = (
            hasattr(self.package_manager, "_should_recommend_swap")
            and self.package_manager._should_recommend_swap()
        )
        if should_recommend_swap and hasattr(self.package_manager, "_has_usable_swap"):
            if not self.package_manager._has_usable_swap():
                if not hasattr(self.package_manager, "_maybe_create_swap") or not self.package_manager._maybe_create_swap(interactive=True):
                    return InstallResult(
                        success=False,
                        message="Low-memory environment detected. Swap creation was declined or failed, so package installation cannot continue safely.",
                        details=(
                            "RAM is low and no swap is active. LoomWG can create a temporary 2 GB swap file "
                            "to continue installation safely."
                        ),
                    )

        if not self._install_packages():
            debug = self.package_manager.last_debug or "No package manager diagnostics were captured."
            details = debug
            if "SIGKILL" in debug or "-9" in debug or self.package_manager._kernel_reported_oom():
                resources = self.package_manager.get_system_resources()
                ram_total = self.package_manager._format_gib(resources["ram_total_kb"])
                ram_available = self.package_manager._format_gib(resources["ram_available_kb"])
                swap_total = self.package_manager._format_gib(resources["swap_total_kb"])
                disk_free = self.package_manager._format_gib(resources["disk_free_bytes"] / 1024 / 1024)
                details = (
                    "Package installation was killed by the operating system.\n"
                    "Reason: Linux OOM killer detected.\n\n"
                    f"Memory:\n"
                    f"  RAM: {ram_total:.1f} GB\n"
                    f"  Available: {ram_available:.1f} GB\n"
                    f"  Swap: {swap_total:.1f} GB\n"
                    f"  Disk free: {disk_free:.1f} GB\n\n"
                    "Recommended action: Create swap space and retry the installation.\n\n"
                    f"{debug}"
                )
            return InstallResult(
                success=False,
                message="Failed to install required packages.",
                details=details,
            )

        if not self._install_rocky_kernel_module():
            return InstallResult(
                success=False,
                message=(
                    "WireGuard kernel support is not available. "
                    "Install ELRepo and kmod-wireguard, then reboot the server."
                ),
            )

        if not self._create_config_directory():
            return InstallResult(
                success=False,
                message="Failed to create /etc/wireguard.",
            )

        if not self._enable_ip_forwarding():
            return InstallResult(
                success=False,
                message="Failed to enable IP forwarding.",
            )

        return InstallResult(
            success=True,
            message="WireGuard packages installed successfully.",
        )

    def _warn_on_low_resources(self) -> None:
        """Display a human-readable memory warning before package installation."""
        if not hasattr(self.package_manager, "get_system_resources"):
            return
        if not hasattr(self.package_manager, "_should_recommend_swap"):
            return

        resources = self.package_manager.get_system_resources()
        ram_total = self.package_manager._format_gib(resources["ram_total_kb"])
        ram_available = self.package_manager._format_gib(resources["ram_available_kb"])
        swap_total = self.package_manager._format_gib(resources["swap_total_kb"])
        if self.package_manager._should_recommend_swap():
            print("\nLow-memory environment detected.")
            print(f"RAM: {ram_total:.1f} GB")
            print(f"Available RAM: {ram_available:.1f} GB")
            print(f"Swap: {swap_total:.1f} GB")
            print(f"Recommended swap size: {self.package_manager.DEFAULT_SWAP_GB} GB")
            if self.package_manager._has_usable_swap():
                print("Swap is already available; continuing with installation.")
            else:
                print("Attempting to create temporary swap before continuing.")

    def _install_packages(self) -> bool:
        """Install required system packages."""

        if not self.package_manager.update():
            return False

        missing_packages = [
            package
            for package in self.REQUIRED_PACKAGES
            if not self.package_manager.is_installed(package)
        ]

        if not missing_packages:
            return True

        return self.package_manager.install(
            missing_packages
        )

    def _install_rocky_kernel_module(self) -> bool:
        """Install the Rocky kernel module package required for wg-quick to work."""
        info = self.detector.detect()
        if info.os_id not in {"rocky", "almalinux", "centos", "rhel"}:
            return True

        if self._wireguard_module_available():
            return True

        for package in self.ROCKY_EXTRA_PACKAGES:
            if not self.package_manager.is_installed(package):
                if not self.package_manager.install([package]):
                    return False

        for package in self.ROCKY_KERNEL_MODULE_CANDIDATES:
            if self.package_manager.is_installed(package):
                return True

            if self.package_manager.install([package]):
                return True

        return self._wireguard_module_available()

    def _wireguard_module_available(self) -> bool:
        """Check whether the kernel WireGuard module is present or loadable."""
        checks = [
            ["modprobe", "wireguard"],
            ["bash", "-lc", "ls /lib/modules/$(uname -r)/kernel/net/wireguard 2>/dev/null"],
            ["bash", "-lc", "ls /lib/modules/$(uname -r)/extra/wireguard 2>/dev/null"],
        ]

        for command in checks:
            result = self.runner.run(command, timeout=30)
            if result.success:
                return True

        return False

    def _create_config_directory(self) -> bool:
        """Create WireGuard configuration directory."""

        try:
            self.config_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.config_dir.chmod(0o700)

            return True

        except OSError:
            return False

    def _enable_ip_forwarding(self) -> bool:
        """Enable IPv4 and IPv6 forwarding."""

        sysctl_file = Path(
            "/etc/sysctl.d/99-loomwg.conf"
        )

        configuration = (
            "net.ipv4.ip_forward = 1\n"
            "net.ipv6.conf.all.forwarding = 1\n"
        )

        try:
            sysctl_file.write_text(
                configuration,
                encoding="utf-8",
            )

        except OSError:
            return False

        result = self.runner.run(
            ["sysctl", "--system"],
            timeout=30,
        )

        return result.success