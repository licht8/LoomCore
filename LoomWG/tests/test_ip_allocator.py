"""Tests for IP allocation."""
import pytest
from loom.wireguard.ip_allocator import IPAllocator


class TestIPAllocator:
    """Test IPAllocator."""

    def test_next_ipv4(self):
        """Test IPv4 allocation."""
        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")

        ip = allocator.get_next_ipv4([])

        assert ip is not None
        assert "10.66.66" in ip

    def test_next_ipv4_avoids_used(self):
        """Test that allocation avoids used IPs."""
        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")

        used = ["10.66.66.2/32"]

        ip = allocator.get_next_ipv4(used)

        assert ip is not None
        assert ip != "10.66.66.2/32"

    def test_validate_ip_in_network_ipv4(self):
        """Test IPv4 validation."""
        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")

        assert allocator.validate_ip_in_network("10.66.66.100", "ipv4")
        assert not allocator.validate_ip_in_network("192.168.1.1", "ipv4")

    def test_validate_ip_in_network_ipv6(self):
        """Test IPv6 validation."""
        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")

        assert allocator.validate_ip_in_network("fd42:42:42::100", "ipv6")
        assert not allocator.validate_ip_in_network("2001:db8::1", "ipv6")

    def test_is_ip_available(self):
        """Test IP availability check."""
        allocator = IPAllocator("10.66.66.0/24", "fd42:42:42::/64")

        used = ["10.66.66.5/32"]

        assert not allocator.is_ip_available("10.66.66.5", used)
        assert allocator.is_ip_available("10.66.66.10", used)
