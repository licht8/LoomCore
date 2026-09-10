"""WireGuard server configuration management."""
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Optional


@dataclass
class ServerConfig:
    """WireGuard server configuration."""

    wg_interface: str
    listen_port: int
    ipv4_network: str
    ipv6_network: str
    dns_primary: str
    dns_secondary: str
    allowed_ips: str
    # These are set by key generation
    private_key: str = ""
    public_key: str = ""

    def validate(self) -> tuple[bool, list[str]]:
        """Validate server configuration."""
        errors = []

        # Validate interface name
        if not self.wg_interface or len(self.wg_interface) > 15:
            errors.append("Invalid interface name (max 15 characters)")

        if not self.wg_interface.isalnum():
            errors.append("Interface name must be alphanumeric")

        # Validate port
        if not (1 <= self.listen_port <= 65535):
            errors.append("Port must be between 1 and 65535")

        # Validate IPv4 network
        try:
            ipv4 = ip_network(self.ipv4_network, strict=False)
            if ipv4.prefixlen < 8 or ipv4.prefixlen > 30:
                errors.append("IPv4 network prefix must be /8 to /30")
        except ValueError:
            errors.append(f"Invalid IPv4 network: {self.ipv4_network}")

        # Validate IPv6 network
        try:
            ipv6 = ip_network(self.ipv6_network, strict=False)
            if ipv6.prefixlen < 48 or ipv6.prefixlen > 126:
                errors.append("IPv6 network prefix must be /48 to /126")
        except ValueError:
            errors.append(f"Invalid IPv6 network: {self.ipv6_network}")

        # Validate DNS addresses
        from ipaddress import IPv4Address, IPv6Address

        try:
            IPv4Address(self.dns_primary)
        except ValueError:
            try:
                IPv6Address(self.dns_primary)
            except ValueError:
                errors.append(f"Invalid primary DNS: {self.dns_primary}")

        try:
            IPv4Address(self.dns_secondary)
        except ValueError:
            try:
                IPv6Address(self.dns_secondary)
            except ValueError:
                errors.append(f"Invalid secondary DNS: {self.dns_secondary}")

        return len(errors) == 0, errors

    @classmethod
    def defaults(cls, interface: str = "wg0") -> "ServerConfig":
        """Create a server config with sensible defaults."""
        return cls(
            wg_interface=interface,
            listen_port=51820,
            ipv4_network="10.66.66.0/24",
            ipv6_network="fd42:42:42::/64",
            dns_primary="1.1.1.1",
            dns_secondary="1.0.0.1",
            allowed_ips="0.0.0.0/0, ::/0",
        )

    @classmethod
    def from_file(cls, config_path: str | Path) -> "ServerConfig":
        """Load a server config file and derive the public key from the private key."""
        path = Path(config_path)
        config = cls(
            wg_interface=path.stem,
            listen_port=51820,
            ipv4_network="10.66.66.0/24",
            ipv6_network="fd42:42:42::/64",
            dns_primary="1.1.1.1",
            dns_secondary="1.0.0.1",
            allowed_ips="0.0.0.0/0, ::/0",
        )

        if not path.exists():
            return config

        private_key = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("PrivateKey") and "=" in stripped:
                _, private_key = stripped.split("=", 1)
                private_key = private_key.strip()
            elif stripped.startswith("ListenPort") and "=" in stripped:
                try:
                    config.listen_port = int(stripped.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif stripped.startswith("Address") and "=" in stripped:
                addresses = [value.strip() for value in stripped.split("=", 1)[1].split(",")]
                for address in addresses:
                    try:
                        network = ip_network(address, strict=False)
                        if network.version == 4:
                            config.ipv4_network = str(network)
                        else:
                            config.ipv6_network = str(network)
                    except ValueError:
                        continue

        config.private_key = private_key
        if private_key:
            from .key_manager import KeyManager

            config.public_key = KeyManager.generate_public_key(private_key)

        return config

    def get_ipv4_network(self) -> IPv4Network:
        """Get IPv4 network object."""
        return ip_network(self.ipv4_network, strict=False)

    def get_ipv6_network(self) -> IPv6Network:
        """Get IPv6 network object."""
        return ip_network(self.ipv6_network, strict=False)

    def get_ipv4_server_address(self) -> str:
        """Get server IPv4 address (.1 of the network)."""
        network = self.get_ipv4_network()
        return str(network.network_address + 1) + "/" + str(network.prefixlen)

    def get_ipv6_server_address(self) -> str:
        """Get server IPv6 address (.1 of the network)."""
        network = self.get_ipv6_network()
        return str(network.network_address + 1) + "/" + str(network.prefixlen)
