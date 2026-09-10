import re
from dataclasses import dataclass
from ipaddress import ip_interface
from pathlib import Path

from ..logging_system.logger import LoomLogger
from ..system.command import CommandRunner


@dataclass
class WireGuardInterface:
    """Information about a WireGuard interface."""

    name: str
    config_path: Path
    active: bool


@dataclass
class WireGuardStartResult:
    """Result of starting and verifying a WireGuard interface."""

    return_code: int
    stdout: str
    stderr: str
    wg_interface_exists: bool
    link_exists: bool
    already_running: bool = False

    @property
    def success(self) -> bool:
        return self.wg_interface_exists and self.link_exists


@dataclass
class WireGuardPeerResult:
    """Result of adding and verifying a peer on a live interface."""

    success: bool
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    interface_present: bool = False
    interface_error: bool = False


class WireGuardManager:
    """Manage WireGuard interfaces."""

    CONFIG_DIR = Path("/etc/wireguard")

    def __init__(self, runner: CommandRunner | None = None):
        self.runner = runner or CommandRunner()

    def is_installed(self) -> bool:
        """Check whether WireGuard tools are installed."""

        result = self.runner.run(["wg", "--version"])

        return result.success

    def list_interfaces(self) -> list[str]:
        """Return available WireGuard interfaces."""

        result = self.runner.run(["wg", "show", "interfaces"])

        if not result.success or not result.stdout:
            return []

        return result.stdout.split()

    def is_interface_active(self, interface: str) -> bool:
        """Check whether a WireGuard interface is active."""

        present, _ = self._check_runtime_interface(interface)
        return present

    def _check_runtime_interface(self, interface: str) -> tuple[bool, str | None]:
        """Return runtime presence and an error from WireGuard inspection."""
        interfaces = self.runner.run(["wg", "show", "interfaces"])
        if interfaces.success and interface in interfaces.stdout.split():
            return True, None

        # Direct inspection is the fallback for builds with unusual list output.
        status = self.runner.run(["wg", "show", interface])
        if status.success and status.stdout:
            return True, None

        if not interfaces.success:
            return False, interfaces.stderr or interfaces.stdout or "wg show interfaces failed"
        if not status.success:
            return False, status.stderr or status.stdout or f"wg show {interface} failed"
        return False, f"Interface {interface} was not listed by WireGuard"

    def is_interface_present(self, interface: str) -> bool:
        """Check whether the actual WireGuard interface exists at runtime."""
        return self.is_interface_active(interface)

    def is_link_present(self, interface: str) -> bool:
        """Check whether the network link exists."""
        result = self.runner.run(["ip", "link", "show", interface])
        return result.success

    @staticmethod
    def _normalize_allowed_ip(value: str, version: int) -> str:
        """Normalize a peer address without adding a second prefix length."""
        address = ip_interface(value)
        if address.version != version:
            raise ValueError(f"Expected IPv{version} address")
        return str(address)

    def get_interfaces(self) -> list[WireGuardInterface]:
        """Return configured WireGuard interfaces."""

        if not self.CONFIG_DIR.exists():
            return []

        interfaces = []

        for config in sorted(self.CONFIG_DIR.glob("*.conf")):
            name = config.stem

            interfaces.append(
                WireGuardInterface(
                    name=name,
                    config_path=config,
                    active=self.is_interface_active(name),
                )
            )

        return interfaces

    def get_status(self, interface: str) -> str | None:
        """Return WireGuard status for an interface."""

        result = self.runner.run(
            ["wg", "show", interface],
        )

        if not result.success:
            return None

        return result.stdout

    def start(self, interface: str) -> bool:
        """Start a WireGuard interface if it is not already active."""

        return self.start_with_result(interface).success

    def start_with_result(self, interface: str) -> WireGuardStartResult:
        """Start an interface and verify both WireGuard and network runtime state."""
        if self.is_interface_active(interface):
            link = self.runner.run(["ip", "link", "show", interface])
            return WireGuardStartResult(
                return_code=0,
                stdout="Interface already active",
                stderr="",
                wg_interface_exists=True,
                link_exists=link.success,
                already_running=True,
            )

        result = self.runner.run(
            ["wg-quick", "up", interface],
            timeout=60,
        )

        wg_interface_exists = self.is_interface_active(interface)
        link_exists = self.is_link_present(interface)
        return WireGuardStartResult(
            return_code=result.return_code,
            stdout=result.stdout,
            stderr=result.stderr,
            wg_interface_exists=wg_interface_exists,
            link_exists=link_exists,
        )

    def stop(self, interface: str) -> bool:
        """Stop a WireGuard interface."""

        result = self.runner.run(
            ["wg-quick", "down", interface],
            timeout=60,
        )

        return result.success

    def restart(self, interface: str) -> bool:
        """Restart a WireGuard interface."""

        if self.is_interface_active(interface):
            if not self.stop(interface):
                return False

        return self.start(interface)

    def sync(self, interface: str) -> bool:
        """Apply configuration changes without restarting the interface."""

        result = self.runner.run(
            [
                "bash",
                "-c",
                f"wg syncconf {interface} <(wg-quick strip {interface})",
            ],
            timeout=60,
        )

        return result.success

    def add_peer_to_interface(
        self,
        interface: str,
        public_key: str,
        client_ip: str,
        logger: LoomLogger | None = None,
        client_ipv6: str | None = None,
    ) -> bool:
        """Dynamically add a peer to a running interface without restarting it."""
        return self.add_peer_with_result(
            interface,
            public_key,
            client_ip,
            client_ipv6=client_ipv6,
            logger=logger,
        ).success

    def remove_peer_from_interface(self, interface: str, public_key: str) -> bool:
        """Remove a peer from the live WireGuard interface."""
        if not self.is_interface_active(interface):
            return True
        return self.runner.run(
            ["wg", "set", interface, "peer", public_key, "remove"], timeout=30
        ).success

    def add_peer_with_result(
        self,
        interface: str,
        public_key: str,
        client_ip: str,
        logger: LoomLogger | None = None,
        client_ipv6: str | None = None,
    ) -> WireGuardPeerResult:
        """Add a peer and return command output suitable for user diagnostics."""
        log = logger or LoomLogger()

        if not interface or not public_key or not client_ip:
            log.error(
                "Peer creation failed: invalid runtime values",
                "peer",
                details=f"interface={interface}, public_key={public_key}, client_ip={client_ip}",
            )
            return WireGuardPeerResult(False, stderr="Invalid runtime values")

        log.info(
            "Peer creation started",
            "peer",
            details=f"interface={interface}, public_key={public_key}",
        )

        interface_present, interface_error = self._check_runtime_interface(interface)
        if not interface_present:
            log.error(
                "Peer creation failed: WireGuard interface not active",
                "peer",
                details=f"interface={interface}, error={interface_error}",
            )
            return WireGuardPeerResult(
                False,
                stderr=interface_error or f"Interface {interface} is not present",
                interface_error=bool(interface_error and "not listed" not in interface_error),
            )

        status = self.get_status(interface)
        if status is None:
            log.error(
                "Peer creation failed: unable to read WireGuard runtime state",
                "peer",
                details=f"interface={interface}",
            )
            return WireGuardPeerResult(False, stderr=f"Unable to read runtime state for {interface}", interface_present=True)

        if re.search(rf"(?m)^peer: {re.escape(public_key)}$", status):
            log.warning(
                "Peer already exists on the running interface",
                "peer",
                details=f"interface={interface}, public_key={public_key}",
            )
            return WireGuardPeerResult(True, interface_present=True)

        try:
            allowed_ips = [self._normalize_allowed_ip(client_ip, 4)]
            if client_ipv6:
                allowed_ips.append(self._normalize_allowed_ip(client_ipv6, 6))
        except ValueError as error:
            message = f"Invalid peer address: {error}"
            log.error("Peer creation failed: invalid AllowedIPs", "peer", details=message)
            return WireGuardPeerResult(False, stderr=message, interface_present=True)

        existing_allowed_ips = set(
            ip.strip()
            for line in status.splitlines()
            if line.strip().lower().startswith("allowed ips:")
            for ip in line.split(":", 1)[1].split(",")
        )
        duplicate_ips = sorted(existing_allowed_ips.intersection(allowed_ips))
        if duplicate_ips:
            message = f"Allowed IP already belongs to another runtime peer: {', '.join(duplicate_ips)}"
            log.error("Peer creation failed: duplicate AllowedIPs", "peer", details=message)
            return WireGuardPeerResult(False, stderr=message, interface_present=True)

        command = [
            "wg",
            "set",
            interface,
            "peer",
            public_key,
            "allowed-ips",
            ",".join(allowed_ips),
        ]
        result = self.runner.run(command, timeout=30)

        if not result.success:
            log.error(
                "Peer creation failed: wg set command failed",
                "peer",
                details=f"command={' '.join(command)} stderr={result.stderr}",
            )
            return WireGuardPeerResult(
                False,
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
                interface_present=True,
            )

        verify = self.get_status(interface)
        if verify is None or public_key not in verify:
            log.error(
                "Peer verification failed: peer not present in wg show output",
                "peer",
                details=f"interface={interface}, public_key={public_key}, output={verify}",
            )
            return WireGuardPeerResult(
                False,
                stderr="Peer was not present after wg set completed",
                interface_present=True,
            )

        log.info(
            "Peer successfully added to wg0",
            "peer",
            details=f"interface={interface}, public_key={public_key}, allowed_ips={','.join(allowed_ips)}",
        )
        log.info(
            "Peer verification successful",
            "peer",
            details=f"interface={interface}, public_key={public_key}",
        )

        return WireGuardPeerResult(True, interface_present=True)

    def read_config(self, interface: str) -> str | None:
        """Read a WireGuard configuration file."""

        config_path = self.CONFIG_DIR / f"{interface}.conf"

        if not config_path.exists():
            return None

        try:
            return config_path.read_text(encoding="utf-8")

        except OSError:
            return None
