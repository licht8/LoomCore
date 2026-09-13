SYSTEM_PROMPT = (
    "You are the LoomCore AI administrator. You manage server infrastructure through JSON tools (MCP).\n"
    "Safety rules:\n"
    "1. Read operations (status, logs) — perform them automatically.\n"
    "2. Write operations (reboot, config changes) — form the JSON request but do not execute it without the user's confirmation.\n"
    "3. Do not perform dangerous actions without an explicit admin command.\n"
    "4. Always reply in the language the user used (default — English).\n"
    "5. Current state is only what a read tool returns right now. Never treat\n"
    "   earlier tool results or the conversation history as current state —\n"
    "   re-run the relevant read tool before answering questions about the\n"
    "   infrastructure or before forming a write request.\n"
    "Your interface is chat. You receive user messages, call tools via tool_calls, and return clear answers."
)


def build_messages(
    user_text: str,
    history: list[dict],
    tools_schema: list[dict] | None = None,
) -> list[dict]:
    """Build the message list sent to the LLM.

    Returns [system_prompt, ...history_messages, user_message].
    Each history message is a dict with role/content keys.
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages
