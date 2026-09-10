"""Generate WireGuard configuration files."""
from rich.console import Console
console = Console()
from pathlib import Path
from typing import Optional


class ConfigGenerator:
    """Generate WireGuard configuration."""

    @staticmethod
    def generate_server_config(
        server_ipv4: str,
        server_ipv6: str,
        listen_port: int,
        private_key: str,
        dns_primary: str | None = None,
        dns_secondary: str | None = None,
        peers: list[dict] | None = None,
    ) -> str:
        """Generate server configuration without resolver integration."""
        if peers is None:
            peers = []

        lines = [
            "[Interface]",
            f"Address = {server_ipv4}, {server_ipv6}",
            f"ListenPort = {listen_port}",
            f"PrivateKey = {private_key}",
        ]

        lines.append("")

        for peer in peers:
            lines.extend(
                [
                    "[Peer]",
                    f"PublicKey = {peer['public_key']}",
                    f"AllowedIPs = {peer['ipv4_address']}, {peer['ipv6_address']}",
                ]
            )

            if peer.get("preshared_key"):
                lines.append(f"PresharedKey = {peer['preshared_key']}")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def validate_server_config(config_content: str) -> tuple[bool, list[str]]:
        """Reject server DNS directives that would invoke unavailable resolvconf."""
        errors = []
        section = None

        for line_number, raw_line in enumerate(config_content.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue

            if section == "Interface" and "=" in line:
                key = line.split("=", 1)[0].strip().lower()
                if key == "dns":
                    errors.append(
                        f"Line {line_number}: DNS is not supported in the server interface"
                    )

        return len(errors) == 0, errors

    @staticmethod
    def generate_peer_config(
        peer_ipv4: str,
        peer_ipv6: str,
        private_key: str,
        server_public_key: str,
        server_endpoint: str,
        server_port: int,
        dns_primary: str,
        dns_secondary: str,
        preshared_key: str | None = None,
    ) -> str:
        """Generate peer/client configuration."""
        lines = [
            "[Interface]",
            f"Address = {peer_ipv4}, {peer_ipv6}",
            f"PrivateKey = {private_key}",
            f"DNS = {dns_primary}, {dns_secondary}",
            "",
            "[Peer]",
            f"PublicKey = {server_public_key}",
            f"Endpoint = {server_endpoint}:{server_port}",
            "AllowedIPs = 0.0.0.0/0, ::/0",
        ]

        if preshared_key:
            lines.append(f"PresharedKey = {preshared_key}")

        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def write_config(config_path: Path, content: str, mode: int = 0o600) -> bool:
        """Write configuration to file with secure permissions."""
        try:
            # Create directory if needed
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # Write with secure permissions
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Set permissions
            config_path.chmod(mode)

            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    @staticmethod
    def append_peer_to_server_config(
        config_path: Path,
        peer_public_key: str,
        peer_ipv4: str,
        peer_ipv6: str,
        preshared_key: str | None = None,
    ) -> bool:
        """Append a peer entry to the server configuration so the tunnel can handshake."""
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)

            if config_path.exists():
                content = config_path.read_text(encoding="utf-8")
            else:
                content = ""

            if "[Peer]" in content and f"PublicKey = {peer_public_key}" in content:
                return True

            if not content.strip().endswith("\n"):
                content += "\n"

            if "[Interface]" not in content:
                content += "[Interface]\nPrivateKey = placeholder\n\n"

            block = [
                "",
                "[Peer]",
                f"PublicKey = {peer_public_key}",
                f"AllowedIPs = {peer_ipv4}, {peer_ipv6}",
            ]
            if preshared_key:
                block.append(f"PresharedKey = {preshared_key}")

            content += "\n".join(block) + "\n"
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(content)

            config_path.chmod(0o600)
            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    @staticmethod
    def remove_peer_from_server_config(config_path: Path, public_key: str) -> bool:
        """Remove exactly one public-key peer block from a server config."""
        try:
            content = config_path.read_text(encoding="utf-8")
            blocks = content.split("[Peer]")
            retained = [blocks[0].rstrip()]
            for block in blocks[1:]:
                if f"PublicKey = {public_key}" not in block:
                    retained.append("[Peer]" + block.rstrip())
            updated = "\n\n".join(part for part in retained if part) + "\n"
            config_path.write_text(updated, encoding="utf-8")
            config_path.chmod(0o600)
            return True
        except OSError:
            return False

    @staticmethod
    def parse_server_peers(config_path: Path) -> list[dict[str, str]]:
        """Read public peer metadata from wg0.conf; never return private keys."""
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            return []
        peers, block = [], None
        for raw in content.splitlines() + ["[end]"]:
            line = raw.strip()
            if line == "[Peer]":
                if block and block.get("public_key") and block.get("allowed_ips"):
                    peers.append(block)
                block = {}
            elif line.startswith("["):
                if block and block.get("public_key") and block.get("allowed_ips"):
                    peers.append(block)
                block = None
            elif block is not None and "=" in line:
                key, value = (part.strip() for part in line.split("=", 1))
                if key == "PublicKey": block["public_key"] = value
                if key == "AllowedIPs": block["allowed_ips"] = value
        return peers

    @staticmethod
    def validate_config(config_content: str) -> tuple[bool, list[str]]:
        """Validate configuration syntax."""
        errors = []

        lines = config_content.strip().split("\n")
        current_section = None
        required_interface_fields = {"Address", "PrivateKey"}
        interface_fields = set()

        for i, line in enumerate(lines, 1):
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if line.startswith("["):
                current_section = line.strip("[]")

                if current_section == "Interface" and interface_fields:
                    missing = required_interface_fields - interface_fields

                    if missing:
                        errors.append(
                            f"Missing Interface fields: {', '.join(missing)}"
                        )

                interface_fields = set()

            elif "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if not value:
                    errors.append(f"Line {i}: Empty value for {key}")

                if current_section == "Interface":
                    interface_fields.add(key)

        # Check final interface section
        if current_section == "Interface":
            missing = required_interface_fields - interface_fields

            if missing:
                errors.append(f"Missing Interface fields: {', '.join(missing)}")

        return len(errors) == 0, errors

    @staticmethod
    def create_backup(
        original_path: Path, backup_dir: Path | None = None
    ) -> Path | None:
        """Create backup of configuration file."""
        if not original_path.exists():
            return None

        try:
            if backup_dir is None:
                backup_dir = original_path.parent / "backups"

            backup_dir.mkdir(parents=True, exist_ok=True)

            # Create timestamped backup
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"{original_path.stem}_{timestamp}.conf"

            backup_path.write_text(original_path.read_text())
            backup_path.chmod(0o600)

            return backup_path
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return None
