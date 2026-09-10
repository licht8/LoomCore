"""WireGuard backup and restore functionality."""
from rich.console import Console
console = Console()
import json
import tarfile
from datetime import datetime
from pathlib import Path


class BackupManager:
    """Manage WireGuard configuration backups."""

    def __init__(self, backup_dir: Path | None = None):
        """Initialize backup manager."""
        if backup_dir is None:
            backup_dir = Path("/var/lib/loomwg/backups")

        self.backup_dir = backup_dir
        self.config_dir = Path("/etc/wireguard")

    def create_backup(self, description: str = "") -> Path | None:
        """Create a backup of WireGuard configuration."""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

            # Create timestamped archive
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = (
                self.backup_dir
                / f"wireguard_backup_{timestamp}.tar.gz"
            )

            # Create metadata
            metadata = {
                "created_at": datetime.now().isoformat(),
                "description": description,
                "config_dir": str(self.config_dir),
            }

            metadata_file = self.backup_dir / f".metadata_{timestamp}.json"
            metadata_file.write_text(json.dumps(metadata, indent=2))

            # Create tar archive
            with tarfile.open(backup_file, "w:gz") as tar:
                # Add WireGuard configuration
                if self.config_dir.exists():
                    tar.add(
                        self.config_dir,
                        arcname="wireguard",
                        filter=self._filter_tar,
                    )

                # Add metadata
                tar.add(metadata_file, arcname=".metadata.json")

            # Set secure permissions
            backup_file.chmod(0o600)

            return backup_file
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return None

    def list_backups(self) -> list[tuple[str, datetime]]:
        """List available backups."""
        if not self.backup_dir.exists():
            return []

        backups = []

        for backup_file in sorted(
            self.backup_dir.glob("wireguard_backup_*.tar.gz"),
            reverse=True,
        ):
            # Extract timestamp from filename
            timestamp_str = (
                backup_file.stem.replace("wireguard_backup_", "")
                .replace(".tar", "")
            )

            try:
                dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                backups.append((backup_file.name, dt))
            except ValueError:
                continue

        return backups

    def restore_backup(
        self, backup_file: Path, create_pre_restore_backup: bool = True
    ) -> bool:
        """Restore from a backup."""
        if not backup_file.exists():
            return False

        try:
            # Create pre-restore backup
            if create_pre_restore_backup:
                pre_restore = self.create_backup(
                    description="Pre-restore backup"
                )

                if not pre_restore:
                    return False

            # Extract backup
            with tarfile.open(backup_file, "r:gz") as tar:
                # Extract to temporary location
                temp_dir = self.backup_dir / ".temp_restore"
                temp_dir.mkdir(parents=True, exist_ok=True)

                tar.extractall(temp_dir)

                # Copy WireGuard config
                temp_config = temp_dir / "wireguard"

                if temp_config.exists():
                    # Backup current config
                    if self.config_dir.exists():

                        shutil.rmtree(self.config_dir)


                    shutil.copytree(temp_config, self.config_dir)

                # Clean up temp directory

                shutil.rmtree(temp_dir)

            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def delete_backup(self, backup_file: Path) -> bool:
        """Delete a backup."""
        try:
            if backup_file.exists():
                backup_file.unlink()

            # Also try to delete metadata
            metadata_file = self.backup_dir / (
                ".metadata_" + backup_file.stem.replace("wireguard_backup_", "")
                + ".json"
            )

            if metadata_file.exists():
                metadata_file.unlink()

            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    def validate_backup(self, backup_file: Path) -> bool:
        """Validate backup integrity."""
        try:
            with tarfile.open(backup_file, "r:gz") as tar:
                # Try to read all members
                for member in tar.getmembers():
                    tar.extractfile(member)

            return True
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/]")
            return False

    @staticmethod
    def _filter_tar(tarinfo):
        """Filter for tar archive to exclude sensitive data."""
        # Exclude private keys from being world-readable
        if tarinfo.isfile():
            tarinfo.mode = 0o600

        return tarinfo
