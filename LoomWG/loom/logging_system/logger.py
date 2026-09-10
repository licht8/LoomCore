import logging
logger = logging.getLogger(__name__)
"""LoomWG logging system."""
import json
from datetime import datetime
from enum import Enum
from pathlib import Path


class LogLevel(Enum):
    """Log level."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LoomLogger:
    """Logger for LoomWG events."""

    def __init__(self, log_dir: Path | None = None):
        """Initialize logger."""
        if log_dir is None:
            log_dir = Path("/var/lib/loomwg/logs")

        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Current log file for today
        self.current_log = self.log_dir / f"loomwg_{datetime.now().strftime('%Y%m%d')}.log"

    def log(
        self,
        level: LogLevel,
        message: str,
        details: str | None = None,
        category: str = "general",
    ) -> None:
        """Log an event."""
        timestamp = datetime.now().isoformat()

        entry = {
            "timestamp": timestamp,
            "level": level.value,
            "category": category,
            "message": message,
        }

        if details:
            entry["details"] = details

        try:
            # Append to log file
            with open(self.current_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            # Set secure permissions
            self.current_log.chmod(0o600)
        except Exception as exc:
            logger.error("Operation failed: %s", exc)

    def info(
        self, message: str, category: str = "general", details: str | None = None
    ) -> None:
        """Log info message."""
        self.log(LogLevel.INFO, message, details, category)

    def warning(
        self, message: str, category: str = "general", details: str | None = None
    ) -> None:
        """Log warning message."""
        self.log(LogLevel.WARNING, message, details, category)

    def error(
        self, message: str, category: str = "general", details: str | None = None
    ) -> None:
        """Log error message."""
        self.log(LogLevel.ERROR, message, details, category)

    def critical(
        self, message: str, category: str = "general", details: str | None = None
    ) -> None:
        """Log critical message."""
        self.log(LogLevel.CRITICAL, message, details, category)

    def log_installation(self, success: bool, details: str | None = None) -> None:
        """Log installation event."""
        message = "WireGuard installation successful" if success else "WireGuard installation failed"
        level = LogLevel.INFO if success else LogLevel.ERROR

        self.log(level, message, details, "installation")

    def log_peer_created(self, peer_name: str, ipv4: str, ipv6: str) -> None:
        """Log peer creation."""
        message = f"Peer '{peer_name}' created"
        details = f"IPv4: {ipv4}, IPv6: {ipv6}"

        self.log(LogLevel.INFO, message, details, "peer")

    def log_peer_removed(self, peer_name: str) -> None:
        """Log peer removal."""
        message = f"Peer '{peer_name}' removed"

        self.log(LogLevel.INFO, message, category="peer")

    def log_peer_revoked(self, peer_name: str, public_key: str) -> None:
        """Log peer revocation without exposing private keys."""
        message = f"Peer '{peer_name}' revoked"
        details = f"Public key: {public_key}"
        self.log(LogLevel.INFO, message, details, category="peer")

    def log_peer_key_rotated(self, peer_name: str, old_public_key: str, new_public_key: str) -> None:
        """Log key rotation while keeping private key data hidden."""
        message = f"Peer '{peer_name}' key rotated"
        details = f"Old public key: {old_public_key}, New public key: {new_public_key}"
        self.log(LogLevel.INFO, message, details, category="peer")

    def log_server_key_rotated(
        self,
        interface: str,
        old_public_key: str,
        new_public_key: str,
        backup_location: str,
        peer_count: int,
    ) -> None:
        """Log server key rotation without exposing the private key."""
        message = f"Server key rotated on {interface}"
        details = (
            f"Old public key: {old_public_key}, New public key: {new_public_key}, "
            f"Backup: {backup_location}, Peers preserved: {peer_count}"
        )
        self.log(LogLevel.INFO, message, details, category="server")

    def log_configuration_changed(
        self, component: str, change: str
    ) -> None:
        """Log configuration change."""
        message = f"Configuration changed: {component}"

        self.log(LogLevel.INFO, message, change, "configuration")

    def log_firewall_change(self, change: str) -> None:
        """Log firewall change."""
        message = f"Firewall configuration changed"

        self.log(LogLevel.INFO, message, change, "firewall")

    def list_recent(self, limit: int = 50) -> list[dict]:
        """Get recent log entries."""
        entries = []

        if not self.current_log.exists():
            return entries

        try:
            with open(self.current_log, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            # Return most recent entries
            return list(reversed(entries[-limit:]))
        except Exception as exc:
            logger.error("Operation failed: %s", exc)
            return []

    def clear_logs(self) -> bool:
        """Clear all log files."""
        try:
            for log_file in self.log_dir.glob("*.log"):
                log_file.unlink()

            return True
        except Exception as exc:
            logger.error("Operation failed: %s", exc)
            return False

    def export_logs(self, export_path: Path) -> bool:
        """Export logs to file."""
        try:
            logs = []

            for log_file in sorted(self.log_dir.glob("*.log")):
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            logs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            export_path.write_text(json.dumps(logs, indent=2))
            export_path.chmod(0o600)

            return True
        except Exception as exc:
            logger.error("Operation failed: %s", exc)
            return False
