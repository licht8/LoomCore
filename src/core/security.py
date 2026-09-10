def classify_tool_risk(tool_name: str) -> str:
    """Определить уровень риска вызова инструмента.

    Возвращает: 'dangerous', 'moderate' или 'safe'.
    """
    explicit_dangerous = {
        "wg_remove_peer",
        "wg_create_client",
        "wg_install",
        "wg_setup_server",
        "wg_start",
        "wg_stop",
        "wg_restart",
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
