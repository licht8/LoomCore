"""IP address allocation for WireGuard peers."""
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address


class IPAllocator:
    """Allocate IP addresses for WireGuard peers."""

    def __init__(self, ipv4_network: str, ipv6_network: str):
        """Initialize with network ranges."""
        self.ipv4_network = IPv4Network(ipv4_network, strict=False)
        self.ipv6_network = IPv6Network(ipv6_network, strict=False)

    def get_next_ipv4(self, used_ips: list[str]) -> str | None:
        """Get next available IPv4 address."""
        used_set = {ip_address(ip.split("/")[0]) for ip in used_ips if "/" in ip}

        # Skip network and server addresses (.0 and .1)
        start = int(self.ipv4_network.network_address) + 2

        for i in range(start, int(self.ipv4_network.broadcast_address)):
            candidate = IPv4Address(i)

            if candidate not in used_set:
                return f"{candidate}/32"

        return None

    def get_next_ipv6(self, used_ips: list[str]) -> str | None:
        """Get next available IPv6 address."""
        used_set = {ip_address(ip.split("/")[0]) for ip in used_ips if "/" in ip}

        # Skip network and server addresses
        start = int(self.ipv6_network.network_address) + 2

        for i in range(start, int(self.ipv6_network.broadcast_address)):
            candidate = IPv6Address(i)

            if candidate not in used_set:
                return f"{candidate}/128"

        return None

    def validate_ip_in_network(
        self, ip_str: str, network_type: str = "ipv4"
    ) -> bool:
        """Validate that an IP belongs to the configured network."""
        try:
            ip = ip_address(ip_str.split("/")[0])

            if network_type == "ipv4":
                return ip in self.ipv4_network
            elif network_type == "ipv6":
                return ip in self.ipv6_network

            return False
        except ValueError:
            return False

    def is_ip_available(
        self, ip_str: str, used_ips: list[str]
    ) -> bool:
        """Check if an IP is available."""
        try:
            ip = ip_address(ip_str.split("/")[0])

            # Check if in any network
            in_ipv4 = ip.version == 4 and ip in self.ipv4_network
            in_ipv6 = ip.version == 6 and ip in self.ipv6_network

            if not (in_ipv4 or in_ipv6):
                return False

            # Check if already used
            for used_ip in used_ips:
                if ip == ip_address(used_ip.split("/")[0]):
                    return False

            return True
        except ValueError:
            return False
