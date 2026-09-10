import logging
logger = logging.getLogger(__name__)
import os
import re
import shutil
import sys
from pathlib import Path

from .command import CommandRunner


class PackageManager:
    """Package management abstraction."""

    DEFAULT_SWAP_GB = 2
    OOM_PATTERNS = (
        r"out of memory",
        r"oom-killer",
        r"killed process",
        r"oom_reaper",
    )

    def __init__(self, runner: CommandRunner | None = None):
        self.runner = runner or CommandRunner()
        self.last_debug: str = ""

    def is_installed(self, package: str) -> bool:
        if self._is_apt_family():
            result = self.runner.run(
                ["bash", "-lc", f"dpkg-query -W -f='${{Status}}' {package} 2>/dev/null | grep -q 'install ok installed'"],
                timeout=30,
            )
            return result.success

        result = self.runner.run(
            ["rpm", "-q", package],
        )

        return result.success

    def install(self, packages: list[str]) -> bool:
        if not packages:
            return True

        if self._is_rhel_family():
            self._ensure_epel_repo()
            self._fix_centos_stream_mirror_once()

        install_command = self._install_command(packages)
        result = self.runner.run(
            install_command,
            timeout=300,
        )
        self.last_debug = self._format_debug_output(install_command, result)

        if result.success:
            return True

        if self._is_sigkill(result):
            if self._recover_from_sigkill(install_command):
                return True
            return False

        if self._is_rhel_family() and self._needs_epel_retry(result):
            if not self._enable_epel():
                return False

            retry_result = self.runner.run(
                install_command,
                timeout=300,
            )
            self.last_debug = self._format_debug_output(install_command, retry_result)
            return retry_result.success

        return False

    @staticmethod
    def _format_debug_output(command: list[str], result) -> str:
        exit_code = getattr(result, "return_code", 1 if not getattr(result, "success", False) else 0)
        lines = [
            f"Command: {' '.join(command)}",
            f"Exit code: {exit_code}{' (SIGKILL)' if exit_code == -9 else ''}",
        ]
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if stdout:
            lines.append(f"stdout: {stdout}")
        if stderr:
            lines.append(f"stderr: {stderr}")
        return "\n".join(lines)

    def remove(self, packages: list[str]) -> bool:
        if not packages:
            return True

        if self._is_apt_family():
            command = ["apt-get", "remove", "-y", *packages]
            result = self.runner.run(command, timeout=300)
            self.last_debug = self._format_debug_output(command, result)
            return result.success

        command = ["dnf", "remove", "-y", *packages]
        result = self.runner.run(
            command,
            timeout=300,
        )
        self.last_debug = self._format_debug_output(command, result)

        return result.success

    def update(self) -> bool:
        if self._is_apt_family():
            command = ["apt-get", "update"]
            result = self.runner.run(command, timeout=300)
            self.last_debug = self._format_debug_output(command, result)
            return result.success

        command = ["dnf", "makecache", "--refresh"]
        result = self.runner.run(
            command,
            timeout=300,
        )
        self.last_debug = self._format_debug_output(command, result)

        if result.success:
            return True

        if self._needs_repo_cache_reset(result):
            clean = self.runner.run(["dnf", "clean", "all"], timeout=300)
            self.last_debug = self._format_debug_output(["dnf", "clean", "all"], clean)
            retry = self.runner.run(command, timeout=300)
            self.last_debug = self._format_debug_output(command, retry)
            return retry.success

        return False

    def get_system_resources(self) -> dict[str, int | float]:
        meminfo = self._read_meminfo()
        disk_free = shutil.disk_usage("/").free
        swap_in_use = self._get_swapon_summary()
        resources = {
            "ram_total_kb": meminfo.get("total_kb", 0),
            "ram_available_kb": meminfo.get("available_kb", 0),
            "swap_total_kb": meminfo.get("swap_total_kb", 0),
            "swap_free_kb": meminfo.get("swap_free_kb", 0),
            "swap_active_kb": swap_in_use["total_kb"],
            "disk_free_bytes": int(disk_free),
        }
        return resources

    def get_resource_summary(self) -> str:
        resources = self.get_system_resources()
        ram_total = self._format_gib(resources["ram_total_kb"])
        ram_available = self._format_gib(resources["ram_available_kb"])
        swap_total = self._format_gib(resources["swap_total_kb"])
        return (
            "System Resources:\n"
            f"RAM: {ram_total:.1f} GB\n"
            f"Available RAM: {ram_available:.1f} GB\n"
            f"Swap: {swap_total:.1f} GB"
        )

    def _read_meminfo(self) -> dict[str, int]:
        path = Path("/proc/meminfo")
        if not path.exists():
            return {"total_kb": 0, "available_kb": 0, "swap_total_kb": 0, "swap_free_kb": 0}

        values: dict[str, int] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            try:
                amount = int(raw_value.strip().split()[0])
            except (IndexError, ValueError):
                continue
            if key == "MemTotal":
                values["total_kb"] = amount
            elif key == "MemAvailable":
                values["available_kb"] = amount
            elif key == "SwapTotal":
                values["swap_total_kb"] = amount
            elif key == "SwapFree":
                values["swap_free_kb"] = amount
        return values

    def _get_swapon_summary(self) -> dict[str, int]:
        result = self.runner.run(["swapon", "--show=SIZE,NAME", "--noheadings"], timeout=20)
        if not result.success or not result.stdout.strip():
            return {"total_kb": 0}

        total_kb = 0
        for line in result.stdout.splitlines():
            pieces = line.split()
            if len(pieces) < 2:
                continue
            size = pieces[0]
            try:
                total_kb += self._parse_swap_size(size)
            except ValueError:
                continue
        return {"total_kb": total_kb}

    @staticmethod
    def _parse_swap_size(value: str) -> int:
        value = value.strip().upper()
        if value.endswith("G"):
            return int(float(value[:-1]) * 1024 * 1024)
        if value.endswith("M"):
            return int(float(value[:-1]) * 1024)
        if value.endswith("K"):
            return int(float(value[:-1]))
        return int(value)

    @staticmethod
    def _format_gib(value_kib: int | float) -> float:
        if not value_kib:
            return 0.0
        return float(value_kib) / 1024.0 / 1024.0

    @staticmethod
    def _needs_epel_retry(result) -> bool:
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        patterns = (
            r"no match for argument",
            r"no matching package",
            r"unable to find a match",
            r"not available",
            r"no package",
            r"package .* not found",
            r"nothing to do",
        )
        return any(re.search(pattern, combined_output) for pattern in patterns)

    @staticmethod
    def _needs_repo_cache_reset(result) -> bool:
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        patterns = (
            r"checksum doesn't match",
            r"failed to download metadata",
            r"cannot download repomd\.xml",
            r"failed to download.*repodata",
            r"metadata.*checksum",
        )
        return any(re.search(pattern, combined_output) for pattern in patterns)

    @staticmethod
    def _is_sigkill(result) -> bool:
        return getattr(result, "return_code", 0) == -9

    def _kernel_reported_oom(self) -> bool:
        check = self.runner.run(
            [
                "bash",
                "-lc",
                "(dmesg -T 2>/dev/null || journalctl -k --no-pager -n 200 2>/dev/null) | grep -E -i 'out of memory|oom-killer|Killed process|oom_reaper' | tail -n 20 || true",
            ],
            timeout=20,
        )
        if not check.success and not check.stdout:
            return False
        combined = f"{check.stdout}\n{check.stderr}".lower()
        return any(re.search(pattern, combined) for pattern in self.OOM_PATTERNS)

    def _has_usable_swap(self) -> bool:
        if self._get_swapon_summary()["total_kb"] > 0:
            return True
        resources = self.get_system_resources()
        return resources.get("swap_total_kb", 0) > 0

    def _disk_has_capacity(self, size_gb: int = DEFAULT_SWAP_GB) -> bool:
        needed_bytes = size_gb * 1024 * 1024 * 1024
        free_bytes = shutil.disk_usage("/").free
        return free_bytes >= needed_bytes

    def _swap_file_exists(self, path: str = "/swapfile") -> bool:
        return Path(path).exists()

    def _has_fstab_entry(self, swap_path: str = "/swapfile") -> bool:
        fstab = Path("/etc/fstab")
        if not fstab.exists():
            return False
        try:
            content = fstab.read_text(encoding="utf-8")
        except OSError:
            return False
        return any(
            line.strip().split()[:2] == [swap_path, "swap"]
            or line.strip().startswith(f"{swap_path} swap")
            for line in content.splitlines()
        )

    def _should_recommend_swap(self) -> bool:
        resources = self.get_system_resources()
        available_ram_mb = self._format_gib(resources["ram_available_kb"]) * 1024.0
        return available_ram_mb < 1024.0 and not self._has_usable_swap()

    def _maybe_create_swap(self, interactive: bool = True, size_gb: int = DEFAULT_SWAP_GB) -> bool:
        if self._swap_file_exists():
            return False
        if self._has_usable_swap():
            return False
        if not self._disk_has_capacity(size_gb):
            return False

        if interactive and sys.stdin.isatty():
            prompt = (
                f"Low available memory detected and no swap is configured.\n"
                f"Creating a {size_gb} GB swap file is recommended for package installation.\n"
                "Create swap? (y/n): "
            )
            try:
                response = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt, OSError):
                logger.error("Input interrupted")
                pause()
                return False
            if response not in {"", "y", "yes"}:
                return False

        swap_path = Path("/swapfile")
        size_bytes = size_gb * 1024 * 1024 * 1024
        if shutil.which("fallocate"):
            command = ["fallocate", "-l", str(size_bytes), str(swap_path)]
        else:
            command = ["dd", "if=/dev/zero", f"of={swap_path}", f"bs=1G", f"count={size_gb}"]

        result = self.runner.run(command, timeout=120)
        if not result.success:
            self.last_debug = self._format_debug_output(command, result)
            return False

        commands = [
            ["chmod", "600", str(swap_path)],
            ["mkswap", str(swap_path)],
            ["swapon", str(swap_path)],
        ]
        for command in commands:
            result = self.runner.run(command, timeout=60)
            if not result.success:
                self.last_debug = self._format_debug_output(command, result)
                return False

        if not self._has_fstab_entry():
            fstab = Path("/etc/fstab")
            try:
                with fstab.open("a", encoding="utf-8") as handle:
                    handle.write("\n/swapfile swap swap defaults 0 0\n")
            except OSError:
                return False

        return self._has_usable_swap()

    def _recover_from_sigkill(self, command: list[str]) -> bool:
        resources = self.get_system_resources()
        available_ram_mb = self._format_gib(resources["ram_available_kb"]) * 1024.0
        has_swap = self._has_usable_swap()
        if available_ram_mb >= 512.0 or has_swap or not self._disk_has_capacity():
            return False
        if not self._kernel_reported_oom():
            return False
        if not self._maybe_create_swap(interactive=False):
            return False
        retry_result = self.runner.run(command, timeout=300)
        self.last_debug = self._format_debug_output(command, retry_result)
        return retry_result.success

    def _fix_centos_stream_mirror_once(self) -> None:
        marker = "/etc/yum.repos.d/.loomwg-centos-mirror-fixed"
        if self.runner.run(["bash", "-lc", "test -f \"/etc/os-release\" && . /etc/os-release && [ \"${ID:-}\" = centos ] && [ -f \"%s\" ]" % marker], timeout=30).success:
            return

        if self.runner.run(["bash", "-lc", ". /etc/os-release 2>/dev/null; [ \"${ID:-}\" = centos ]"], timeout=30).success:
            stream_version = self._centos_stream_version()
            if not stream_version:
                return
            for repo_file in (
                "/etc/yum.repos.d/centos.repo",
                "/etc/yum.repos.d/centos-stream.repo",
                "/etc/yum.repos.d/CentOS-Stream.repo",
            ):
                if not self.runner.run(["bash", "-lc", f"test -f \"{repo_file}\""], timeout=30).success:
                    continue
                self.runner.run(["bash", "-lc", f"cp \"{repo_file}\" \"{repo_file}.loomwg.bak\" 2>/dev/null || true"], timeout=30)
                self.runner.run(["bash", "-lc", f"sed -i 's|^metalink=.*centos-baseos-\\$stream.*|baseurl=https://mirror.stream.centos.org/{stream_version}-stream/BaseOS/x86_64/os/|' \"{repo_file}\""], timeout=30)
                self.runner.run(["bash", "-lc", f"sed -i 's|^metalink=.*centos-appstream-\\$stream.*|baseurl=https://mirror.stream.centos.org/{stream_version}-stream/AppStream/x86_64/os/|' \"{repo_file}\""], timeout=30)
                self.runner.run(["bash", "-lc", f"touch \"{marker}\""], timeout=30)
                break

    def _centos_stream_version(self) -> str | None:
        result = self.runner.run(
            ["bash", "-lc", ". /etc/os-release 2>/dev/null; if [ \"${ID:-}\" = centos ]; then printf '%s' \"${VERSION_ID:-}\"; fi"],
            timeout=30,
        )
        if not result.success:
            return None
        version = result.stdout.strip()
        if version.startswith("9"):
            return "9"
        if version.startswith("10"):
            return "10"
        return None

    def _ensure_epel_repo(self) -> bool:
        if self._epel_installed():
            return True
        enabled = self._enable_epel()
        if not enabled:
            self.last_debug = "Failed to enable EPEL repository.\n" + (self.last_debug or "")
        return enabled

    def _epel_installed(self) -> bool:
        result = self.runner.run(["bash", "-lc", "rpm -q epel-release >/dev/null 2>&1 || ls /etc/yum.repos.d/epel*.repo >/dev/null 2>&1"], timeout=30)
        return result.success

    def _is_apt_family(self) -> bool:
        result = self.runner.run(
            ["bash", "-lc", ". /etc/os-release 2>/dev/null; printf '%s' \"${ID:-}\"; printf '\n'; printf '%s' \"${ID_LIKE:-}\""],
            timeout=30,
        )
        if not result.success:
            return False

        combined = " ".join(part for part in [result.stdout, result.stderr] if part).lower()
        if any(token in combined for token in ("ubuntu", "debian", "raspbian")):
            return True
        if any(token in combined for token in ("rocky", "almalinux", "centos", "rhel", "fedora")):
            return False
        return False

    def _install_command(self, packages: list[str]) -> list[str]:
        if self._is_apt_family():
            return ["apt-get", "install", "-y", *packages]
        return ["dnf", "install", "-y", *packages]

    def _is_rhel_family(self) -> bool:
        result = self.runner.run(
            ["bash", "-lc", ". /etc/os-release 2>/dev/null; printf '%s' \"${ID:-}\"; printf '\n'; printf '%s' \"${ID_LIKE:-}\""],
            timeout=30,
        )
        if result.success:
            combined = " ".join(part for part in [result.stdout, result.stderr] if part).lower()
            if any(token in combined for token in ("rocky", "almalinux", "centos", "rhel", "fedora")):
                return True
            if any(token in combined for token in ("ubuntu", "debian", "raspbian")):
                return False

        return (
            self.runner.run(["bash", "-lc", "command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1"], timeout=30).success
        )

    def _enable_epel(self) -> bool:
        install_epel = self.runner.run(
            ["dnf", "install", "-y", "epel-release"],
            timeout=300,
        )
        self.last_debug = self._format_debug_output(["dnf", "install", "-y", "epel-release"], install_epel)

        if install_epel.success:
            return True

        version_result = self.runner.run(
            ["bash", "-lc", "rpm -E %rhel"],
            timeout=30,
        )
        self.last_debug = self._format_debug_output(["bash", "-lc", "rpm -E %rhel"], version_result)

        if not version_result.success:
            return False

        rhel = version_result.stdout.strip()
        url = (
            f"https://dl.fedoraproject.org/pub/epel/epel-release-latest-{rhel}.noarch.rpm"
        )

        direct_install = self.runner.run(
            ["dnf", "install", "-y", url],
            timeout=300,
        )
        self.last_debug = self._format_debug_output(["dnf", "install", "-y", url], direct_install)

        return direct_install.success

    def get_version(self, package: str) -> str | None:
        result = self.runner.run(
            ["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", package],
        )

        if not result.success:
            return None

        return result.stdout