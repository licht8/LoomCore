import secrets

from fastapi import HTTPException

from src.auth import decode_jwt_token
from src.config import settings


def require_admin_token(token: str | None) -> None:
    """Проверить административный доступ (JWT-cookie или bearer-токен)."""
    if settings.admin_password_hash and settings.admin_totp_secret:
        payload = decode_jwt_token(token, settings.session_secret)
        if not payload:
            raise HTTPException(status_code=401, detail="Требуется вход администратора")
        return
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Административный доступ не настроен")
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Требуется административный токен")


def classify_tool_risk(tool_name: str) -> str:
    """Определить уровень риска вызова инструмента.

    Возвращает: 'dangerous', 'moderate' или 'safe'.
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
