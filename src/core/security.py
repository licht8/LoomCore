import re
import secrets

from fastapi import HTTPException

from src.auth import decode_jwt_token
from src.config import settings


def admin_jwt_mode_enabled() -> bool:
    """Return True when admin auth uses JWT sessions instead of a bearer token."""
    return bool(settings.admin_password_hash and settings.admin_totp_secret)


def _valid_jwt(token: str | None) -> bool:
    """Return True when the token is a valid admin session JWT."""
    if not admin_jwt_mode_enabled():
        return False
    return decode_jwt_token(token, settings.session_secret) is not None


def _valid_static_token(token: str | None) -> bool:
    """Return True when the token matches the configured static admin token."""
    if not settings.admin_token or not token:
        return False
    return secrets.compare_digest(token, settings.admin_token)


def require_admin_token(token: str | None) -> None:
    """Verify admin access via a JWT session or the static admin token.

    Both credentials are accepted regardless of the configured auth mode, so an
    operator can hand out the static token even when password+TOTP is enabled.
    """
    if _valid_jwt(token) or _valid_static_token(token):
        return
    if not admin_jwt_mode_enabled() and not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin access is not configured")
    if admin_jwt_mode_enabled():
        raise HTTPException(status_code=401, detail="Admin login required")
    raise HTTPException(status_code=401, detail="Admin token required")


VALID_TOOL_RISKS = frozenset({"safe", "moderate", "dangerous"})


def classify_tool_risk(tool_name: str, declared: str | None = None) -> str:
    """Determine the risk level of a tool call.

    A plugin- or core-declared risk (``declared``) takes precedence over the
    name heuristics. Returns: 'dangerous', 'moderate' or 'safe'.
    """
    if isinstance(declared, str) and declared.strip().lower() in VALID_TOOL_RISKS:
        return declared.strip().lower()

    explicit_dangerous = {
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
    # Read-only tools that must never sit behind approval, even though the
    # heuristic keywords below would otherwise flag them (e.g. "..._config").
    explicit_safe = {
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

    tool_lower = tool_name.lower()
    if tool_lower in explicit_dangerous:
        return "dangerous"
    if tool_lower in explicit_safe:
        return "safe"

    # Match whole underscore/dash-separated tokens so substrings like "rm" in
    # "form" (or "add" in "address") no longer trigger a false positive.
    tokens = {token for token in re.split(r"[^a-z0-9]+", tool_lower) if token}
    dangerous = {"restart", "delete", "drop", "reboot", "rm", "chmod", "flush"}
    moderate = {"create", "add", "set", "config", "deploy"}

    if tokens & dangerous:
        return "dangerous"
    if tokens & moderate:
        return "moderate"
    return "safe"
