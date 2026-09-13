import secrets

from fastapi import HTTPException

from src.auth import decode_jwt_token
from src.config import settings


def admin_jwt_mode_enabled() -> bool:
    """Return True when admin auth uses JWT sessions instead of a bearer token."""
    return bool(settings.admin_password_hash and settings.admin_totp_secret)


def require_admin_token(token: str | None) -> None:
    """Verify admin access (JWT cookie or bearer token)."""
    if admin_jwt_mode_enabled():
        payload = decode_jwt_token(token, settings.session_secret)
        if not payload:
            raise HTTPException(status_code=401, detail="Admin login required")
        return
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin access is not configured")
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Admin token required")


def classify_tool_risk(tool_name: str) -> str:
    """Determine the risk level of a tool call.

    Returns: 'dangerous', 'moderate' or 'safe'.
    """
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
    if tool_name.lower() in explicit_dangerous:
        return "dangerous"

    dangerous = ["restart", "delete", "drop", "reboot", "rm", "chmod", "flush"]
    moderate = ["create", "add", "set", "config", "deploy"]

    tool_lower = tool_name.lower()
    if any(kw in tool_lower for kw in dangerous):
        return "dangerous"
    if any(kw in tool_lower for kw in moderate):
        return "moderate"
    return "safe"
