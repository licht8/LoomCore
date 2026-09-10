"""Tests for WireGuard installer prerequisites."""

from loom.system.info import SystemCheck, SystemDetector, SystemInfo
from loom.system.packages import PackageManager
from loom.wireguard.installer import WireGuardInstaller


class TestWireGuardInstaller:
    """Validate installation prerequisites."""

    def test_required_packages_do_not_require_resolvconf(self):
        """Rocky 10 does not ship the `resolvconf` package, so we must not require it."""
        assert "resolvconf" not in WireGuardInstaller.REQUIRED_PACKAGES

    def test_supported_rhel_family_oses_pass_system_checks(self, monkeypatch):
        """CentOS Stream 10 and other RHEL family systems should be accepted."""

        def fake_detect(self):
            return SystemInfo(
                os_id="centos",
                os_name="CentOS Stream 10 (Coughlan)",
                os_version="10",
                kernel="6.12.0",
                architecture="x86_64",
                hostname="test-host",
                is_root=True,
                init_system="systemd",
                package_manager="dnf",
                firewalld_available=False,
                firewalld_running=False,
                wireguard_available=False,
                public_ip="203.0.113.10",
                default_interface="eth0",
            )

        monkeypatch.setattr(SystemDetector, "detect", fake_detect)
        checks = SystemDetector().check()

        assert checks[0].passed is True
        assert checks[0].message == "CentOS Stream 10 (Coughlan)"

    def test_install_ignores_missing_firewalld_before_package_install(self, monkeypatch):
        """Installing should continue when firewalld is absent but will be installed automatically."""

        class FakePackageManager:
            def update(self):
                return True

            def is_installed(self, package):
                return False

            def install(self, packages):
                return True

        monkeypatch.setattr(
            SystemDetector,
            "check",
            lambda self: [
                SystemCheck("Operating system", True, "CentOS Stream 10 (Coughlan)"),
                SystemCheck("Architecture", True, "x86_64"),
                SystemCheck("Root privileges", True, "Running as root"),
                SystemCheck("Init system", True, "systemd"),
                SystemCheck("Package manager", True, "dnf"),
                SystemCheck("firewalld", False, "Not installed"),
            ],
        )

        installer = WireGuardInstaller(
            package_manager=FakePackageManager(),
            wireguard=None,
        )
        monkeypatch.setattr(installer, "_install_rocky_kernel_module", lambda: True)
        monkeypatch.setattr(installer, "_create_config_directory", lambda: True)
        monkeypatch.setattr(installer, "_enable_ip_forwarding", lambda: True)

        result = installer.install("wg0")

        assert result.success is True

    def test_package_manager_retries_with_epel_when_wireguard_package_missing(self, monkeypatch):
        """Some CentOS/RHEL packages require EPEL before wireguard-tools is available."""

        class DummyResult:
            def __init__(self, stdout="", stderr="", success=False):
                self.stdout = stdout
                self.stderr = stderr
                self.success = success

        calls = []
        retry_once = {"done": False}

        class DummyRunner:
            def run(self, command, timeout=300):
                calls.append(command)
                if command[:2] == ["dnf", "install"] and "epel-release" in command:
                    return DummyResult(success=True)
                if command[:3] == ["dnf", "install", "-y"] and not retry_once["done"]:
                    retry_once["done"] = True
                    return DummyResult(
                        stderr="No matching package to install: wireguard-tools",
                        success=False,
                    )
                return DummyResult(success=True)

        pm = PackageManager(runner=DummyRunner())

        assert pm.install(["wireguard-tools", "firewalld"]) is True
        assert any("epel-release" in cmd for cmd in calls)

    def test_package_manager_enables_epel_on_rhel_family_before_install(self):
        """CentOS/Red Hat servers should enable EPEL before installing WireGuard packages."""

        class DummyResult:
            def __init__(self, stdout="", stderr="", success=False):
                self.stdout = stdout
                self.stderr = stderr
                self.success = success

        calls = []

        class DummyRunner:
            def run(self, command, timeout=300):
                calls.append(command)
                if command[:2] == ["bash", "-lc"] and "os-release" in command[2]:
                    return DummyResult(stdout="centos", success=True)
                if command[:2] == ["bash", "-lc"] and "rpm -q epel-release" in command[2]:
                    return DummyResult(success=False)
                return DummyResult(success=True)

        pm = PackageManager(runner=DummyRunner())
        assert pm._is_rhel_family() is True
        assert pm._ensure_epel_repo() is True
        assert any("epel-release" in command for command in calls)

    def test_package_manager_refreshes_metadata_after_checksum_mismatch(self):
        """Broken DNF repo metadata should be cleaned and retried automatically."""

        class DummyResult:
            def __init__(self, stdout="", stderr="", success=False):
                self.stdout = stdout
                self.stderr = stderr
                self.success = success

        states = {"first_refresh": False, "cleaned": False, "retry": False}

        class DummyRunner:
            def run(self, command, timeout=300):
                if command == ["dnf", "makecache", "--refresh"] and not states["first_refresh"]:
                    states["first_refresh"] = True
                    return DummyResult(
                        stdout="CentOS Stream 10 - BaseOS",
                        stderr="Errors during downloading metadata for repository 'baseos': checksum doesn't match",
                        success=False,
                    )
                if command == ["dnf", "clean", "all"]:
                    states["cleaned"] = True
                    return DummyResult(success=True)
                if command == ["dnf", "makecache", "--refresh"] and states["first_refresh"] and states["cleaned"]:
                    states["retry"] = True
                    return DummyResult(stdout="metadata refreshed", success=True)
                return DummyResult(success=True)

        pm = PackageManager(runner=DummyRunner())
        assert pm.update() is True
        assert states["cleaned"] is True
        assert states["retry"] is True

    def test_centos_stream_mirror_uses_detected_major_version(self):
        """CentOS Stream 9 and 10 should each point to the correct official mirror."""

        class DummyResult:
            def __init__(self, stdout="", stderr="", success=True):
                self.stdout = stdout
                self.stderr = stderr
                self.success = success

        class DummyRunner:
            def __init__(self, version):
                self.version = version
                self.commands = []

            def run(self, command, timeout=300):
                self.commands.append(command)
                if command[:2] == ["bash", "-lc"] and "VERSION_ID" in command[2]:
                    return DummyResult(stdout=self.version)
                if command[:2] == ["bash", "-lc"] and "test -f \"/etc/os-release\"" in command[2]:
                    return DummyResult(success=True)
                if command[:2] == ["bash", "-lc"] and "test -f \"/etc/yum.repos.d/centos.repo\"" in command[2]:
                    return DummyResult(success=True)
                return DummyResult(success=True)

        pm_9 = PackageManager(runner=DummyRunner("9"))
        assert pm_9._centos_stream_version() == "9"

        pm_10 = PackageManager(runner=DummyRunner("10"))
        assert pm_10._centos_stream_version() == "10"

    def test_memory_detection_reads_proc_meminfo(self, monkeypatch):
        """The host memory and swap counters should be readable from Linux /proc data."""
        pm = PackageManager()
        monkeypatch.setattr(pm, "_read_meminfo", lambda: {"total_kb": 2048000, "available_kb": 512000, "swap_total_kb": 0, "swap_free_kb": 0})
        monkeypatch.setattr("shutil.disk_usage", lambda path: type("Disk", (), {"free": 8 * 1024 * 1024 * 1024})())
        resources = pm.get_system_resources()
        assert resources["ram_total_kb"] > 0
        assert resources["disk_free_bytes"] > 0

    def test_system_detector_includes_memory_checks(self):
        """System checks should include RAM, swap, and disk space reporting."""
        checks = SystemDetector().check()
        names = {check.name for check in checks}
        assert "RAM" in names
        assert "Swap" in names
        assert "Disk space" in names

    def test_package_manager_detects_sigkill(self):
        """A negative exit code of -9 should be interpreted as SIGKILL."""

        class DummyResult:
            return_code = -9
            stdout = ""
            stderr = ""
            success = False

        assert PackageManager._is_sigkill(DummyResult()) is True

    def test_package_manager_detects_oom_messages(self, monkeypatch):
        """Kernel OOM diagnostics should be recognized from dmesg/journalctl output."""

        class DummyRunner:
            def run(self, command, timeout=300):
                return type("Result", (), {"success": True, "stdout": "Out of memory: Killed process 1234", "stderr": ""})()

        pm = PackageManager(runner=DummyRunner())
        assert pm._kernel_reported_oom() is True

    def test_package_manager_rejects_existing_swapfile(self, monkeypatch):
        """Existing swap files should not be overwritten by the installer."""
        pm = PackageManager()
        monkeypatch.setattr(pm, "_swap_file_exists", lambda path="/swapfile": True)
        monkeypatch.setattr(pm, "_has_usable_swap", lambda: False)
        assert pm._maybe_create_swap(interactive=False) is False

    def test_package_manager_skips_swap_on_sufficient_disk_space(self, monkeypatch):
        """Low memory alone should not trigger swap creation if a swap device already exists."""
        pm = PackageManager()
        monkeypatch.setattr(pm, "_has_usable_swap", lambda: True)
        monkeypatch.setattr(pm, "_disk_has_capacity", lambda size_gb=2: True)
        assert pm._maybe_create_swap(interactive=False) is False

    def test_package_manager_retries_after_sigkill_when_swap_is_needed(self, monkeypatch):
        """A single SIGKILL retry should be attempted after creating swap."""

        class DummyResult:
            def __init__(self, return_code=0, stdout="", stderr=""):
                self.return_code = return_code
                self.stdout = stdout
                self.stderr = stderr
                self.success = return_code == 0

        class DummyRunner:
            def __init__(self):
                self.calls = 0

            def run(self, command, timeout=300):
                self.calls += 1
                if command == ["dnf", "install", "-y", "wireguard-tools"] and self.calls == 1:
                    return DummyResult(return_code=-9, stderr="Killed process")
                return DummyResult(stdout="installed", return_code=0)

        runner = DummyRunner()
        pm = PackageManager(runner=runner)
        monkeypatch.setattr(pm, "_kernel_reported_oom", lambda: True)
        monkeypatch.setattr(pm, "_maybe_create_swap", lambda interactive=True, size_gb=2: True)
        monkeypatch.setattr(pm, "_has_usable_swap", lambda: False)
        monkeypatch.setattr(pm, "_disk_has_capacity", lambda size_gb=2: True)
        monkeypatch.setattr(pm, "get_system_resources", lambda: {"ram_total_kb": 2048000, "ram_available_kb": 200000, "swap_total_kb": 0, "swap_free_kb": 0, "disk_free_bytes": 8 * 1024 * 1024 * 1024})

        assert pm.install(["wireguard-tools"]) is True

    def test_installer_attempts_swap_creation_before_package_install(self, monkeypatch):
        """Low-memory systems should create swap before trying to install required packages."""

        class FakePackageManager:
            DEFAULT_SWAP_GB = 2

            def __init__(self):
                self.last_debug = ""
                self.calls = []

            def get_system_resources(self):
                return {"ram_total_kb": 2 * 1024 * 1024, "ram_available_kb": 200 * 1024, "swap_total_kb": 0, "swap_free_kb": 0, "disk_free_bytes": 8 * 1024 * 1024 * 1024}

            def _format_gib(self, value_kib):
                return float(value_kib) / 1024.0 / 1024.0

            def _should_recommend_swap(self):
                return True

            def _has_usable_swap(self):
                return False

            def _maybe_create_swap(self, interactive=True, size_gb=2):
                self.calls.append((interactive, size_gb))
                return True

            def update(self):
                return True

            def is_installed(self, package):
                return False

            def install(self, packages):
                return True

        installer = WireGuardInstaller(package_manager=FakePackageManager())
        monkeypatch.setattr(SystemDetector, "check", lambda self: [SystemCheck("Operating system", True, "CentOS Stream 9")])
        monkeypatch.setattr(installer, "_install_rocky_kernel_module", lambda: True)
        monkeypatch.setattr(installer, "_create_config_directory", lambda: True)
        monkeypatch.setattr(installer, "_enable_ip_forwarding", lambda: True)

        result = installer.install("wg0")

        assert result.success is True
        assert installer.package_manager.calls == [(True, 2)]

    def test_package_manager_uses_apt_for_debian_family(self):
        """Debian and Ubuntu should use apt-get for package installs instead of dnf."""

        class DummyResult:
            def __init__(self, stdout="", stderr="", success=True):
                self.stdout = stdout
                self.stderr = stderr
                self.success = success

        class DummyRunner:
            def run(self, command, timeout=300):
                if command == ["bash", "-lc", "command -v apt-get >/dev/null 2>&1"]:
                    return DummyResult(success=True)
                if command == ["bash", "-lc", ". /etc/os-release 2>/dev/null; printf '%s' \"${ID:-}\"; printf '\n'; printf '%s' \"${ID_LIKE:-}\""]:
                    return DummyResult(stdout="ubuntu\n", success=True)
                return DummyResult(success=True)

        pm = PackageManager(runner=DummyRunner())
        assert pm._is_apt_family() is True
        assert pm._install_command(["wireguard", "qrencode"]) == ["apt-get", "install", "-y", "wireguard", "qrencode"]
