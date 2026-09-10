"""Storage for exported peer config artifacts."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from .interfaces import DEFAULT_INTERFACE, get_selected_interface

try:
    import qrcode
except ImportError:  # pragma: no cover - installation dependency
    qrcode = None


def _project_root() -> Path:
    """Return the workspace root containing this app."""
    return Path(__file__).resolve().parents[2]


class ClientConfigStore:
    """Save generated peer config files and QR codes in a dedicated directory."""

    def __init__(
        self,
        base_dir: str | Path | None = None,
        qr_dir: str | Path | None = None,
        interface_name: str | None = None,
    ):
        interface_name = interface_name or get_selected_interface()
        if base_dir is None:
            data_dir = _project_root() / "data"
            base_dir = (
                data_dir / "clients"
                if interface_name == DEFAULT_INTERFACE
                else data_dir / "interfaces" / interface_name / "clients"
            )

        self.base_dir = Path(base_dir)
        self.qr_dir = (
            Path(qr_dir)
            if qr_dir is not None
            else (
                _project_root() / "data" / "qr_codes"
                if interface_name == DEFAULT_INTERFACE
                else _project_root() / "data" / "interfaces" / interface_name / "qr_codes"
            )
        )

    def ensure_directory(self) -> Path:
        """Create the client config directory if it does not exist."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def save_peer_config(self, peer_name: str, config_content: str) -> Path:
        """Save a peer .conf file to the client config directory."""
        self.ensure_directory()

        config_path = self.base_dir / f"{peer_name}.conf"
        config_path.write_text(config_content, encoding="utf-8")
        config_path.chmod(0o600)
        return config_path

    def save_qr_code(self, peer_name: str, config_content: str) -> Path | None:
        """Create a QR code image for a peer config in the project data directory."""
        if qrcode is None:
            return None

        self.qr_dir.mkdir(parents=True, exist_ok=True)

        qr_path = self.qr_dir / f"{peer_name}.png"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(config_content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        return qr_path

    def mark_server_key_rotation_required(self, peer_names: list[str] | None = None) -> Path:
        """Mark saved configs as stale after a server key rotation."""
        self.ensure_directory()
        names = sorted(set(peer_names or []))
        marker_path = self.base_dir / ".server_key_rotation_required.json"
        payload = {
            "updated_at": datetime.now().isoformat(),
            "reason": "server_key_rotation",
            "peer_names": names,
        }
        marker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        marker_path.chmod(0o600)
        return marker_path

    def list_server_key_rotation_required(self) -> list[str]:
        """Return the peer names that need their config regenerated after a server key rotation."""
        marker_path = self.base_dir / ".server_key_rotation_required.json"
        if not marker_path.exists():
            return []
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8"))
                names = payload.get("peer_names", [])
                if isinstance(names, list):
                    return [str(item) for item in names]
            except (TypeError, ValueError):
                return []
            return []

    def regenerate_after_server_key_rotation(
            self,
            server_public_key: str,
            peer_names: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
            """Update saved client configs and QR codes without changing client private keys."""
            names = sorted(set(peer_names or []))
            if not names:
                names = sorted(path.stem for path in self.base_dir.glob("*.conf"))

            regenerated: list[str] = []
            failed: list[str] = []
            for name in names:
                config_path = self.base_dir / f"{name}.conf"
                if not config_path.is_file():
                    failed.append(name)
                    continue

                content = config_path.read_text(encoding="utf-8")
                updated, replacements = re.subn(
                    r"(?m)^(\s*PublicKey\s*=\s*).*$",
                    rf"\g<1>{server_public_key}",
                    content,
                    count=1,
                )
                if replacements != 1:
                    failed.append(name)
                    continue

                config_path.write_text(updated, encoding="utf-8")
                config_path.chmod(0o600)
                if self.save_qr_code(name, updated) is None:
                    failed.append(name)
                    continue
                regenerated.append(name)

            return regenerated, failed

    @staticmethod
    def render_qr(config_content: str) -> str | None:
        """Render a QR code as terminal text for immediate phone scanning."""
        if qrcode is None:
            return None

        qr = qrcode.QRCode(version=1, box_size=1, border=2)
        qr.add_data(config_content)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        return "\n".join(
            "".join("██" if cell else "  " for cell in row)
            for row in matrix
        )

    @staticmethod
    def render_qr_ansi(config_content: str) -> str | None:
        """Render a QR code in ANSIUTF8 format using the qrencode system utility."""
        if shutil.which("qrencode") is None:
            return None
        try:
            result = subprocess.run(
                ["qrencode", "-t", "ANSIUTF8"],
                input=config_content,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                return None
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
