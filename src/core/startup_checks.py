"""Startup security-posture checks.

Kept separate from the app assembly so the checks can be unit-tested directly.
Warnings go to the ``loomcore.security`` logger; a contradictory hard requirement
(``REQUIRE_TLS=true`` with insecure cookies) fails closed at startup.
"""

import logging

from src.auth import revocation_store_path
from src.config import settings
from src.core.security import admin_jwt_mode_enabled
from src.llm.config import get_active

logger = logging.getLogger("loomcore.security")


def _check_auth_mode() -> None:
    """Warn when both credentials are configured without an explicit AUTH_MODE."""
    if settings.auth_mode.strip():
        return
    if settings.admin_token and admin_jwt_mode_enabled():
        logger.warning(
            "Both ADMIN_TOKEN and password+TOTP are configured; defaulting to "
            "jwt only. Set AUTH_MODE=both if the static token must keep working."
        )


def _check_tls() -> None:
    """Fail closed when TLS is required but cookies are insecure; warn otherwise."""
    if settings.require_tls and not settings.secure_cookies:
        raise RuntimeError(
            "REQUIRE_TLS=true but SECURE_COOKIES=false: refusing to start with "
            "insecure session cookies."
        )
    if not settings.secure_cookies:
        logger.warning(
            "SECURE_COOKIES=false: the session cookie may travel in cleartext. "
            "Enable the TLS block in deploy/nginx/default.conf or set "
            "SECURE_COOKIES=true."
        )


def _check_revocation_store() -> None:
    """Warn when revoked sessions cannot be persisted to the configured path."""
    path = revocation_store_path()
    if path is None:
        logger.warning(
            "LOOMCORE_REVOKED_FILE is empty: revoked sessions are kept in memory "
            "only and will be lost on restart."
        )
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        logger.warning(
            "Revocation store '%s' cannot be persisted to (%s); falling back to "
            "in-memory and logout will not survive a restart.",
            path,
            exc,
        )


def _check_llm_provider() -> None:
    """Warn when no LLM provider is configured yet (chat replies with a hint)."""
    if not get_active().configured:
        logger.warning(
            "No LLM provider is configured; chat will reply with a setup hint "
            "until an admin chooses one in Settings."
        )


def check_security_posture() -> None:
    """Run all startup security-posture checks (warnings and fail-closed errors)."""
    _check_auth_mode()
    _check_tls()
    _check_revocation_store()
    _check_llm_provider()
