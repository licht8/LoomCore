import re
import secrets
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from src.auth import decode_jwt_token
from src.config import settings
from src.core.client_ip import client_ip
from src.core.security_log import log_security_event

VALID_AUTH_MODES = frozenset({"token", "jwt", "both"})


def is_https(request: Any) -> bool:
    """Return True when the request reached us over TLS.

    Works for both HTTP requests and WebSocket handshakes: a WebSocket scope
    reports ``ws``/``wss`` rather than ``http``/``https``. Behind a
    TLS-terminating proxy the scheme comes from ``X-Forwarded-Proto``; that
    header is only trusted because the app binds to loopback, so only the local
    reverse proxy can reach it. A missing request is treated as insecure (fail
    closed).
    """
    if request is None:
        return False
    headers = getattr(request, "headers", None)
    url = getattr(request, "url", None)
    proto = headers.get("x-forwarded-proto") if headers is not None else None
    if not proto and url is not None:
        proto = url.scheme
    if not proto:
        return False
    return proto.split(",")[0].strip().lower() in {"https", "wss"}


def admin_jwt_mode_enabled() -> bool:
    """Return True when password+TOTP admin credentials are configured."""
    return bool(settings.admin_password_hash and settings.admin_totp_secret)


def auth_mode() -> str:
    """Resolve the active admin auth mode: ``token``, ``jwt`` or ``both``.

    An explicit ``AUTH_MODE`` wins; otherwise the mode is inferred from what is
    configured, preferring ``jwt`` so a configured password+TOTP cannot be
    silently bypassed by a leftover static token.
    """
    configured = settings.auth_mode.strip().lower()
    if configured in VALID_AUTH_MODES:
        return configured
    return "jwt" if admin_jwt_mode_enabled() else "token"


def _valid_jwt(token: str | None) -> bool:
    """Return True when the token is a valid admin session JWT."""
    if not admin_jwt_mode_enabled():
        return False
    return decode_jwt_token(token, settings.session_secret) is not None


def _valid_static_token(token: str | None) -> bool:
    """Return True when the token matches the configured static admin token."""
    if not settings.admin_token or not token:
        return False
    return secrets.compare_digest(
        token.encode("utf-8"), settings.admin_token.encode("utf-8")
    )


def require_admin_token(token: str | None, request: Request | None = None) -> None:
    """Verify admin access via the credentials allowed by the active auth mode.

    ``AUTH_MODE`` selects which of the two credentials are accepted: a JWT
    session (``jwt``), the static admin token (``token``), or both. A valid
    credential that the active mode disables is rejected with 403 rather than
    silently accepted, so a configured password+TOTP cannot be bypassed by a
    leftover static token. When ``REQUIRE_TLS`` is set, a non-TLS request is
    rejected (403) regardless of which credential it carries. Failures are
    recorded as security events (the client IP is included when a ``request``
    is supplied).
    """
    mode = auth_mode()
    client = client_ip(request) if request is not None else None

    # TLS is enforced for every credential path (session cookie and static
    # Bearer token), not just the password login: a static token over plain HTTP
    # is just as exposed as a password.
    if settings.require_tls and not is_https(request):
        log_security_event("auth_failure", reason="tls_required", client=client)
        raise HTTPException(status_code=403, detail="TLS is required")

    jwt_configured = admin_jwt_mode_enabled()
    token_configured = bool(settings.admin_token)

    if mode == "jwt":
        available = jwt_configured
    elif mode == "token":
        available = token_configured
    else:
        available = jwt_configured or token_configured
    if not available:
        log_security_event("auth_failure", reason="not_configured", client=client)
        raise HTTPException(status_code=503, detail="Admin access is not configured")

    if mode in {"jwt", "both"} and _valid_jwt(token):
        return
    if mode in {"token", "both"} and _valid_static_token(token):
        return

    if mode == "jwt" and _valid_static_token(token):
        log_security_event("auth_failure", reason="static_token_disabled", client=client)
        raise HTTPException(
            status_code=403, detail="Static admin token is disabled (AUTH_MODE=jwt)"
        )
    if mode == "token" and _valid_jwt(token):
        log_security_event("auth_failure", reason="jwt_disabled", client=client)
        raise HTTPException(
            status_code=403, detail="JWT sessions are disabled (AUTH_MODE=token)"
        )
    if jwt_configured:
        log_security_event("auth_failure", reason="login_required", client=client)
        raise HTTPException(status_code=401, detail="Admin login required")
    log_security_event("auth_failure", reason="invalid_token", client=client)
    raise HTTPException(status_code=401, detail="Admin token required")


def is_valid_admin_credential(token: str | None) -> bool:
    """Return True when ``token`` is a credential the active auth mode accepts.

    Non-raising counterpart of :func:`require_admin_token` for callers that only
    need to know whether a credential is genuine (e.g. rate-limit keying). TLS
    enforcement and security-event logging stay in ``require_admin_token``.
    """
    if not token:
        return False
    mode = auth_mode()
    return (mode in {"jwt", "both"} and _valid_jwt(token)) or (
        mode in {"token", "both"} and _valid_static_token(token)
    )


VALID_TOOL_RISKS = frozenset({"safe", "moderate", "dangerous"})

# Tools that must always sit behind approval.
_EXPLICIT_DANGEROUS = frozenset(
    {
        "wg_remove_peer",
        "wg_export_existing_clients",
        "wg_setup_server",
        "wg_interface_start",
        "wg_interface_stop",
        "wg_interface_restart",
        "wg_service_enable",
        "wg_service_disable",
        "wg_install_dependencies",
        "core_enable_plugin",
        "core_disable_plugin",
        "core_reload_plugin",
        "ops_run_command",
        "ops_write_file",
        "ops_kill_process",
        "ops_systemctl_restart",
    }
)

# Read-only tools that must never sit behind approval, even though the
# heuristic keywords below would otherwise flag them (e.g. "..._config").
_EXPLICIT_SAFE = frozenset(
    {
        "wg_list_interfaces",
        "wg_list_peers",
        "wg_interface_status",
        "wg_show_interface_config",
        "wg_check_interface_config",
        "wg_get_client_artifact",
        "wg_diagnostics",
        "wg_stats",
        "ops_system_metrics",
        "ops_disk_usage",
        "ops_scan_health",
        "ops_list_processes",
        "ops_dmesg",
        "ops_failed_units",
        "ops_log_search",
        "ops_network_connections",
        "ops_network_status",
        "ops_read_file",
        "ops_systemctl_status",
    }
)

# Whole underscore/dash-separated tokens, matched against a name's tokens so
# substrings like "rm" in "form" (or "add" in "address") do not trigger.
_DANGEROUS_TOKENS = frozenset(
    {
        "restart",
        "delete",
        "drop",
        "reboot",
        "rm",
        "chmod",
        "flush",
        "remove",
        "kill",
        "stop",
        "wipe",
        "purge",
        "uninstall",
    }
)
_MODERATE_TOKENS = frozenset(
    {"create", "add", "set", "config", "deploy", "install", "disable", "enable"}
)


def classify_tool_risk(tool_name: str, declared: str | None = None) -> str:
    """Determine the risk level of a tool call.

    A plugin- or core-declared risk (``declared``) takes precedence over the
    name heuristics. Returns: 'dangerous', 'moderate' or 'safe'.
    """
    if isinstance(declared, str) and declared.strip().lower() in VALID_TOOL_RISKS:
        return declared.strip().lower()

    tool_lower = tool_name.lower()
    if tool_lower in _EXPLICIT_DANGEROUS:
        return "dangerous"
    if tool_lower in _EXPLICIT_SAFE:
        return "safe"

    tokens = {token for token in re.split(r"[^a-z0-9]+", tool_lower) if token}
    if tokens & _DANGEROUS_TOKENS:
        return "dangerous"
    if tokens & _MODERATE_TOKENS:
        return "moderate"
    return "safe"
