"""Tests for configuration generation."""
import pytest
from loom.wireguard.config_generator import ConfigGenerator


class TestConfigGenerator:
    """Test ConfigGenerator."""

    def test_generate_server_config(self):
        """Test server config generation."""
        gen = ConfigGenerator()

        config = gen.generate_server_config(
            server_ipv4="10.66.66.1/24",
            server_ipv6="fd42:42:42::1/64",
            listen_port=51820,
            private_key="private_key_here",
            dns_primary="1.1.1.1",
            dns_secondary="1.0.0.1",
        )

        assert "[Interface]" in config
        assert "10.66.66.1/24" in config
        assert "fd42:42:42::1/64" in config
        assert "51820" in config
        assert "private_key_here" in config
        assert "DNS =" not in config

        valid, errors = gen.validate_server_config(config)
        assert valid
        assert errors == []

    def test_server_config_rejects_dns_directive(self):
        """Server DNS must be rejected because wg-quick invokes resolvconf."""
        config = """[Interface]
Address = 10.66.66.1/24, fd42:42:42::1/64
ListenPort = 51820
PrivateKey = private_key_here
DNS = 1.1.1.1, 1.0.0.1
"""

        valid, errors = ConfigGenerator.validate_server_config(config)

        assert not valid
        assert "DNS is not supported" in errors[0]

    def test_generate_peer_config(self):
        """Test peer config generation."""
        gen = ConfigGenerator()

        config = gen.generate_peer_config(
            peer_ipv4="10.66.66.2/32",
            peer_ipv6="fd42:42:42::2/128",
            private_key="peer_private_key",
            server_public_key="server_public_key",
            server_endpoint="1.2.3.4",
            server_port=51820,
            dns_primary="1.1.1.1",
            dns_secondary="1.0.0.1",
        )

        assert "[Interface]" in config
        assert "10.66.66.2/32" in config
        assert "peer_private_key" in config
        assert "[Peer]" in config
        assert "server_public_key" in config
        assert "1.2.3.4:51820" in config

    def test_validate_config(self):
        """Test config validation."""
        gen = ConfigGenerator()

        config = """[Interface]
Address = 10.66.66.1/24, fd42:42:42::1/64
ListenPort = 51820
PrivateKey = some_private_key

[Peer]
PublicKey = some_public_key
AllowedIPs = 10.66.66.2/32
"""

        valid, errors = gen.validate_config(config)

        assert valid
        assert len(errors) == 0

    def test_validate_config_invalid(self):
        """Test config validation with invalid config."""
        gen = ConfigGenerator()

        config = """[Interface]
ListenPort = 51820
"""

        valid, errors = gen.validate_config(config)

        assert not valid
        assert len(errors) > 0
