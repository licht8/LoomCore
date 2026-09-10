"""Tests for server config peer registration."""

from pathlib import Path

from loom.wireguard.config_generator import ConfigGenerator


class TestPeerServerConfig:
    """Server config should include peer entries for active clients."""

    def test_append_peer_to_server_config(self, tmp_path):
        """New peers must be added to the server config so the tunnel can complete handshakes."""
        config_path = tmp_path / "wg0.conf"
        config_path.write_text(
            "[Interface]\n"
            "Address = 10.66.66.1/24, fd42:42:42::1/64\n"
            "ListenPort = 51820\n"
            "PrivateKey = server_private_key\n\n"
        )

        result = ConfigGenerator.append_peer_to_server_config(
            config_path,
            "peer_public_key",
            "10.66.66.2/32",
            "fd42:42:42::2/128",
        )

        assert result is True
        content = config_path.read_text()
        assert "[Peer]" in content
        assert "peer_public_key" in content
        assert "10.66.66.2/32" in content
        assert "fd42:42:42::2/128" in content
