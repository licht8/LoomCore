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

# Values once shipped verbatim in .env.example. Rejecting them at startup keeps a
# copy-paste deployment from verifying admin JWTs against a publicly-known key.
_PLACEHOLDER_CREDENTIALS = frozenset(
    {
        "generate-with-the-auth-helper",
        "base32-secret",
        "generate-a-long-random-session-secret",
    }
)


def _check_placeholder_credentials() -> None:
    """Fail closed when a shipped .env.example placeholder is still in use.

    ``.env.example`` previously shipped publicly-known values for these fields,
    so a deployment that copied it and edited only ``POSTGRES_PASSWORD`` would
    have enabled JWT auth signed with a known secret. Empty values are always
    allowed (they simply leave JWT/password login off).
    """
    fields = {
        "ADMIN_PASSWORD_HASH": settings.admin_password_hash,
        "ADMIN_TOTP_SECRET": settings.admin_totp_secret,
        "SESSION_SECRET": settings.session_secret,
        "ADMIN_TOKEN": settings.admin_token,
    }
    offenders = sorted(
        name
        for name, value in fields.items()
        if value.strip() in _PLACEHOLDER_CREDENTIALS
    )
    if offenders:
        raise RuntimeError(
            "Refusing to start: placeholder credential(s) still set for "
            + ", ".join(offenders)
            + ". Replace them with real values (empty is allowed)."
        )


def _check_auth_mode() -> None:
    """Warn when both credentials are configured without an explicit AUTH_MODE."""
    if settings.auth_mode.strip():
        return
    if settings.admin_token and admin_jwt_mode_enabled():
        logger.warning(
            "Both ADMIN_TOKEN and password+TOTP are configured; defaulting to "
            "jwt only. Set AUTH_MODE=both if the static token must keep working."
        )


def _check_jwt_mode_credentials() -> None:
    """Warn when JWT mode is selected but no JWT-capable credential exists.

    ``AUTH_MODE=jwt``/``both`` passes config validation with just a
    ``SESSION_SECRET``, yet every password+TOTP login and session check 503s
    because no ``admin_password_hash`` + ``admin_totp_secret`` pair exists. With
    ``AUTH_MODE=both`` and a static ``ADMIN_TOKEN`` admin access still works via
    that token, so the warning then names only the unusable JWT path.
    """
    mode = settings.auth_mode.strip().lower()
    if mode not in {"jwt", "both"}:
        return
    if not settings.session_secret or admin_jwt_mode_enabled():
        return
    if mode == "both" and settings.admin_token:
        logger.warning(
            "AUTH_MODE=both but ADMIN_PASSWORD_HASH/ADMIN_TOTP_SECRET are not "
            "both set: JWT sessions can never authenticate; only the static "
            "ADMIN_TOKEN works."
        )
        return
    logger.warning(
        "AUTH_MODE=%s but ADMIN_PASSWORD_HASH/ADMIN_TOTP_SECRET are not both "
        "set: JWT sessions can never authenticate and admin access will 503.",
        mode,
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
        try:
            # Create-only: never truncate an existing store, which a concurrent
            # writer may have just populated.
            with path.open("x", encoding="utf-8") as handle:
                handle.write("{}\n")
        except FileExistsError:
            # Existing store: prove it is writable without touching its contents
            # (a 0-byte file already reads as an empty store).
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
    active = get_active()
    if not active.configured:
        logger.warning(
            "No LLM provider is configured; chat will reply with a setup hint "
            "until an admin chooses one in Settings."
        )
    if active.enable_thinking:
        logger.warning(
            "LLM thinking/reasoning is enabled; this can roughly double turn "
            "latency. Disable it in Settings unless you need it."
        )


def check_security_posture() -> None:
    """Run all startup security-posture checks (warnings and fail-closed errors)."""
    _check_placeholder_credentials()
    _check_auth_mode()
    _check_jwt_mode_credentials()
    _check_tls()
    _check_revocation_store()
    _check_llm_provider()
