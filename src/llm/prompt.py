SYSTEM_PROMPT = (
    "Ты — LoomHub AI-администратор. Ты управляешь серверной инфраструктурой через JSON-инструменты (MCP).\n"
    "Правила безопасности:\n"
    "1. Операции чтения (статус, логи) — выполняй автоматически.\n"
    "2. Операции записи (перезагрузка, изменение конфигов) — формируй JSON-запрос, но не выполняй без подтверждения пользователя.\n"
    "3. Не выполняй опасных действий без явной команды администратора.\n"
    "4. Всегда отвечай на языке, который использовал пользователь (по умолчанию — русский).\n"
    "Твой интерфейс — чат. Ты получаешь сообщения от пользователей, вызываешь инструменты через tool_calls и возвращаешь понятные ответы."
)


def build_messages(
    user_text: str,
    history: list[dict],
    tools_schema: list[dict] | None = None,
) -> list[dict]:
    """Собрать список сообщений для отправки LLM.

    Возвращает: [system_prompt, ...history_messages, user_message].
    Каждый history_message — dict с ключами role/content.
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages
