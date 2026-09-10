def classify_tool_risk(tool_name: str) -> str:
    """Определить уровень риска вызова инструмента.

    Возвращает: 'dangerous', 'moderate' или 'safe'.
    """
    dangerous = ["restart", "delete", "drop", "reboot", "rm", "chmod", "flush"]
    moderate = ["create", "add", "set", "config", "deploy"]

    tool_lower = tool_name.lower()
    if any(kw in tool_lower for kw in dangerous):
        return "dangerous"
    if any(kw in tool_lower for kw in moderate):
        return "moderate"
    return "safe"
