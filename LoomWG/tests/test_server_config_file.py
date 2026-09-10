"""Tests for server config parsing and peer config generation."""

from loom.wireguard.key_manager import KeyManager
from loom.wireguard.server_config import ServerConfig


class TestServerConfigFile:
    """Validate reading real server keys from a wg config file."""

    def test_from_file_populates_server_public_key(self, tmp_path):
        """A server config file should supply the correct server public key."""
        private_key = KeyManager.generate_private_key()
        public_key = KeyManager.generate_public_key(private_key)

        config_path = tmp_path / "wg0.conf"
        config_path.write_text(
            "[Interface]\n"
            f"PrivateKey = {private_key}\n"
            "Address = 10.66.66.1/24, fd42:42:42::1/64\n"
            "ListenPort = 51820\n"
        )

        parsed = ServerConfig.from_file(config_path)

        assert parsed.private_key == private_key
        assert parsed.public_key == public_key

    def test_normalize_wireguard_config_adds_space_after_addresses(self):
        """Canonical config formatting should add spaces after comma-delimited addresses."""
        from loom.cli import normalize_wireguard_config

        normalized = normalize_wireguard_config(
            "[Interface]\n"
            "Address=10.66.66.1/24,fd42:42:42::1/64\n"
            "PrivateKey=abc\n"
            "ListenPort=51820\n"
        )

        assert "Address = 10.66.66.1/24, fd42:42:42::1/64" in normalized
        assert "PrivateKey = abc" in normalized
        assert "ListenPort = 51820" in normalized

    def test_repair_wireguard_config_file_normalizes_legacy_format(self, tmp_path):
        """Repairing a legacy config file should rewrite the malformed lines in place."""
        from loom.cli import repair_wireguard_config_file

        config_path = tmp_path / "wg0.conf"
        config_path.write_text(
            "[Interface]\n"
            "Address=10.66.66.1/24,fd42:42:42::1/64\n"
            "PrivateKey=abc\n"
            "ListenPort=51820\n"
        )

        repaired = repair_wireguard_config_file(config_path)

        assert "Address = 10.66.66.1/24, fd42:42:42::1/64" in repaired
        assert config_path.read_text() == repaired
