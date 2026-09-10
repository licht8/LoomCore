"""Centralized validation for WireGuard peer AllowedIPs and interface state."""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Iterable, Sequence


@dataclass
class ValidationCheck:
    """Single validation result."""

    name: str
    passed_: bool
    details: str = ""
    peer_name: str | None = None

    @property
    def passed(self) -> bool:
        return self.passed_

    def format(self) -> str:
        icon = "✓" if self.passed_ else "✗"
        prefix = f"{icon} {self.name}"
        if self.peer_name:
            return f"{prefix} ({self.peer_name})"
        return prefix if not self.details else f"{prefix}: {self.details}"


@dataclass
class ValidationResult:
    """Structured validation result for peer configuration."""

    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def errors(self) -> list[str]:
        return [check.details or check.name for check in self.checks if not check.passed]

    def add(self, name: str, passed: bool, details: str = "", peer_name: str | None = None) -> None:
        self.checks.append(ValidationCheck(name=name, passed_=passed, details=details, peer_name=peer_name))

    def format(self) -> str:
        lines = ["VALID:"] if self.valid else ["INVALID:"]
        for check in self.checks:
            lines.append(check.format())
        return "\n".join(lines)


class AllowedIPValidationManager:
    """Validate peer AllowedIPs before they reach WireGuard live state or config files."""

    def __init__(self, vpn_networks: Sequence[str] | None = None):
        self.vpn_networks = tuple(vpn_networks or ("10.66.66.0/24", "fd42:42:42::/64"))

    @staticmethod
    def _split_allowed_ips(raw_allowed_ips: str | Sequence[str]) -> list[str]:
        if raw_allowed_ips is None:
            return []
        if isinstance(raw_allowed_ips, str):
            items = raw_allowed_ips.split(",")
        else:
            items = list(raw_allowed_ips)
        cleaned = []
        for item in items:
            value = str(item).strip()
            if value:
                cleaned.append(value)
        return cleaned

    @staticmethod
    def _parse_network(value: str):
        item = str(value).strip()
        if not item:
            raise ValueError("empty AllowedIPs entry")
        if item.count("/") > 1:
            raise ValueError(f"malformed CIDR: {item}")
        return ip_network(item, strict=False)

    @staticmethod
    def _parse_address(value: str):
        item = str(value).strip()
        if not item:
            raise ValueError("empty address")
        if item.count("/") > 1:
            raise ValueError(f"malformed address: {item}")
        return ip_address(item.split("/", 1)[0])

    def validate_allowed_ips(
        self,
        allowed_ips: str | Sequence[str],
        peer_name: str | None = None,
        existing_peers: Iterable[object] | None = None,
        server_networks: Sequence[str] | None = None,
        server_addresses: Sequence[str] | None = None,
    ) -> ValidationResult:
        """Validate one peer's AllowedIPs against syntax, VPN membership, and conflict rules."""
        result = ValidationResult()
        candidate_entries = self._split_allowed_ips(allowed_ips)
        server_nets = [ip_network(net, strict=False) for net in (server_networks or self.vpn_networks)]
        server_addrs = []
        for addr in (server_addresses or []):
            if not addr or not str(addr).strip():
                continue
            item = str(addr).strip()
            try:
                server_addrs.append(ip_address(item.split("/", 1)[0]))
            except ValueError:
                try:
                    network = ip_network(item, strict=False)
                    server_addrs.append(network.network_address)
                except ValueError:
                    continue

        if not candidate_entries:
            result.add("Address set", False, "No AllowedIPs provided")
            return result

        seen_networks: list[object] = []
        for entry in candidate_entries:
            try:
                network = self._parse_network(entry)
                if network.version not in (4, 6):
                    raise ValueError(f"Unsupported IP family: {entry}")
                if network.version == 4 and not (0 <= network.prefixlen <= 32):
                    raise ValueError(f"Invalid IPv4 prefix length: {entry}")
                if network.version == 6 and not (0 <= network.prefixlen <= 128):
                    raise ValueError(f"Invalid IPv6 prefix length: {entry}")
                self._parse_address(entry)
                seen_networks.append(network)
            except ValueError as exc:
                result.add("CIDR syntax", False, f"Invalid AllowedIP {entry}: {exc}", peer_name)
                return result

        if len(candidate_entries) == len(set(candidate_entries)):
            result.add("CIDR format", True, "AllowedIPs are syntactically valid")
        else:
            result.add("CIDR format", False, "Duplicate AllowedIP entries detected", peer_name)

        for net in seen_networks:
            matches_vpn = any(
                net.version == vpn.version and (net.subnet_of(vpn) or net == vpn)
                for vpn in server_nets
            )
            if not matches_vpn:
                result.add("VPN subnet", False, f"AllowedIP {net} does not belong to the configured VPN network", peer_name)
                break
        else:
            result.add("VPN subnet", True, "AllowedIPs belong to the configured VPN subnet")

        if existing_peers is not None:
            for existing in existing_peers:
                if existing is None:
                    continue
                candidate_name = getattr(existing, "name", "")
                if peer_name and candidate_name == peer_name:
                    continue
                for current in seen_networks:
                    for other in self._collect_peer_networks(existing):
                        if current.version == other.version and current.overlaps(other):
                            result.add("Address availability", False, f"AllowedIP overlap with peer '{candidate_name}'", peer_name)
                            return result
            result.add("Address availability", True, "No overlapping peer AllowedIPs were detected")

            for existing in existing_peers:
                if existing is None:
                    continue
                candidate_name = getattr(existing, "name", "")
                if peer_name and candidate_name == peer_name:
                    continue
                for current in seen_networks:
                    for other in self._collect_peer_networks(existing):
                        if current.version == other.version and current == other:
                            result.add("Duplicate address", False, f"AllowedIP already assigned to peer '{candidate_name}'", peer_name)
                            return result
            result.add("Duplicate address", True, "No duplicate peer addresses detected")

        if server_addrs:
            for addr in server_addrs:
                for net in seen_networks:
                    if net.version == addr.version and addr in net:
                        result.add("Server address", False, f"AllowedIP conflicts with server address {addr}", peer_name)
                        return result
            result.add("Server address", True, "No peer AllowedIP matches the server address")

        return result

    @staticmethod
    def _collect_peer_networks(peer: object) -> list[object]:
        networks = []
        for attr in ("ipv4_address", "ipv6_address"):
            value = getattr(peer, attr, None)
            if value:
                try:
                    networks.append(ip_network(str(value), strict=False))
                except ValueError:
                    continue
        return networks


AllowedIPsValidationManager = AllowedIPValidationManager
AllowedIPValidator = AllowedIPValidationManager
AllowedIPsManager = AllowedIPValidationManager


__all__ = [
    "AllowedIPValidationManager",
    "AllowedIPsValidationManager",
    "AllowedIPValidator",
    "AllowedIPsManager",
    "ValidationResult",
    "ValidationCheck",
]
