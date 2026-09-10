"""System service management."""
import logging
import subprocess
from dataclasses import dataclass


@dataclass
class ServiceStatus:
    """Status of a system service."""

    name: str
    is_active: bool
    is_enabled: bool
    description: str | None = None


logger = logging.getLogger(__name__)


class ServiceManager:
    """Manage system services."""

    def __init__(self):
        pass

    def start(self, service: str) -> bool:
        """Start a service."""
        try:
            result = subprocess.run(
                ["systemctl", "start", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service start failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def stop(self, service: str) -> bool:
        """Stop a service."""
        try:
            result = subprocess.run(
                ["systemctl", "stop", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service stop failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def restart(self, service: str) -> bool:
        """Restart a service."""
        try:
            result = subprocess.run(
                ["systemctl", "restart", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service restart failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def reload(self, service: str) -> bool:
        """Reload a service."""
        try:
            result = subprocess.run(
                ["systemctl", "reload", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service reload failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def enable(self, service: str) -> bool:
        """Enable a service (start on boot)."""
        try:
            result = subprocess.run(
                ["systemctl", "enable", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service enable failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def disable(self, service: str) -> bool:
        """Disable a service (don't start on boot)."""
        try:
            result = subprocess.run(
                ["systemctl", "disable", service],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service disable failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def is_active(self, service: str) -> bool:
        """Check if a service is currently active."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service active check failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def is_enabled(self, service: str) -> bool:
        """Check if a service is enabled."""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", service],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.error("Service enabled check failed for %s: %s", service, exc.stderr)
            return False
        except Exception as exc:
            logger.error("Service operation failed: %s", exc)
            return False

    def get_status(self, service: str) -> ServiceStatus:
        """Get full status of a service."""
        return ServiceStatus(
            name=service,
            is_active=self.is_active(service),
            is_enabled=self.is_enabled(service),
        )

    def get_status_text(self, service: str) -> str | None:
        """Get full status text for a service."""
        try:
            result = subprocess.run(
                ["systemctl", "status", service],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            return result.stdout if result.returncode == 0 else None
        except subprocess.CalledProcessError as exc:
            logger.error("Service status query failed for %s: %s", service, exc.stderr)
            return None
        except Exception as exc:
            logger.error("Service status query failed: %s", exc)
            return None