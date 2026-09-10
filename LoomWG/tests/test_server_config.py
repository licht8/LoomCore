"""Tests for WireGuard configuration."""
import pytest
from loom.wireguard.server_config import ServerConfig


class TestServerConfig:
    """Test ServerConfig."""

    def test_defaults(self):
        """Test default configuration."""
        config = ServerConfig.defaults()

        assert config.wg_interface == "wg0"
        assert config.listen_port == 51820
        assert config.ipv4_network == "10.66.66.0/24"
        assert config.ipv6_network == "fd42:42:42::/64"

    def test_validate_valid_config(self):
        """Test validation of valid config."""
        config = ServerConfig.defaults()

        valid, errors = config.validate()

        assert valid
        assert len(errors) == 0

    def test_validate_invalid_port(self):
        """Test validation with invalid port."""
        config = ServerConfig.defaults()
        config.listen_port = 99999

        valid, errors = config.validate()

        assert not valid
        assert len(errors) > 0

    def test_validate_invalid_interface(self):
        """Test validation with invalid interface name."""
        config = ServerConfig.defaults()
        config.wg_interface = "this_is_way_too_long_for_interface_name"

        valid, errors = config.validate()

        assert not valid
        assert len(errors) > 0

    def test_ipv4_network(self):
        """Test IPv4 network parsing."""
        config = ServerConfig.defaults()

        network = config.get_ipv4_network()

        assert str(network) == "10.66.66.0/24"

    def test_ipv6_network(self):
        """Test IPv6 network parsing."""
        config = ServerConfig.defaults()

        network = config.get_ipv6_network()

        assert str(network) == "fd42:42:42::/64"

    def test_server_addresses(self):
        """Test server address generation."""
        config = ServerConfig.defaults()

        ipv4 = config.get_ipv4_server_address()
        ipv6 = config.get_ipv6_server_address()

        assert "10.66.66.1" in ipv4
        assert "fd42:42:42::1" in ipv6
