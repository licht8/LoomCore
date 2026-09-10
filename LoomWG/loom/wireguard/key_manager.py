"""WireGuard key management."""
from rich.console import Console
console = Console()
import subprocess
from dataclasses import dataclass


@dataclass
class KeyPair:
    """A WireGuard public/private key pair."""

    private_key: str
    public_key: str


class KeyManager:
    """Manage WireGuard keys."""

    @staticmethod
    def generate_private_key() -> str:
        """Generate a private key."""
        try:
            result = subprocess.run(
                ["wg", "genkey"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            return result.stdout.strip()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            raise RuntimeError(f"Failed to generate private key: {exc}")

    @staticmethod
    def generate_public_key(private_key: str) -> str:
        """Generate public key from private key."""
        try:
            result = subprocess.run(
                ["wg", "pubkey"],
                input=private_key,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            return result.stdout.strip()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            raise RuntimeError(f"Failed to generate public key: {exc}")

    @staticmethod
    def generate_preshared_key() -> str:
        """Generate a preshared key."""
        try:
            result = subprocess.run(
                ["wg", "genpsk"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

            return result.stdout.strip()
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            raise RuntimeError(f"Failed to generate preshared key: {exc}")

    @classmethod
    def generate_keypair(cls) -> KeyPair:
        """Generate a complete keypair."""
        private_key = cls.generate_private_key()
        public_key = cls.generate_public_key(private_key)

        return KeyPair(
            private_key=private_key,
            public_key=public_key,
        )

    @staticmethod
    def validate_key(key: str) -> bool:
        """Validate a key format."""
        # WireGuard keys are base64-encoded 32-byte values
        # They should be 43 or 44 characters long
        if not key or len(key) < 43:
            return False

        try:
            import base64

            decoded = base64.b64decode(key)
            return len(decoded) == 32
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False
