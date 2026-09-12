import secrets

from fastapi import HTTPException

from src.auth import decode_jwt_token
from src.config import settings


def require_admin_token(token: str | None) -> None:
    """Verify admin access (JWT cookie or bearer token)."""
    if settings.admin_password_hash and settings.admin_totp_secret:
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
        "wg_start",
        "wg_stop",
        "wg_restart",
        "wg_service_enable",
        "wg_service_disable",
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
