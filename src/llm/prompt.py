from pathlib import Path

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


PLANNER_PROMPT = (
    "You are the LoomCore planner. You never execute anything yourself.\n"
    "For any request that needs tools, call `agent_plan` exactly once with a short\n"
    "goal and an ordered list of steps. Each step names a tool from the catalog\n"
    "below, the arguments to pass, and a concise reason (why).\n"
    "Use only tool names that appear in the catalog. Prefer the fewest steps that\n"
    "achieve the request. If no tool is needed, reply with plain text instead of a\n"
    "plan. The plan is the only output that matters; do not describe your intended\n"
    "behavior in prose."
)

SUMMARY_INSTRUCTION = (
    "Summarize the completed plan for the user, following the voice guide above. "
    "Be brief and concrete."
)

DEFAULT_SOUL = (
    "You are Loom. Speak in the first person, plainly and concisely. "
    "Report results, not mechanics."
)

_SOUL_PATH = Path(__file__).with_name("SOUL.md")


def load_soul(path: str | Path | None = None) -> str:
    """Load the voice document; fall back to a minimal default if missing."""
    target = Path(path) if path else _SOUL_PATH
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_SOUL


def build_planner_messages(
    user_text: str,
    history: list[dict],
    tool_catalog: str,
) -> list[dict]:
    """Build planner messages: system (planner + catalog), history, user."""
    system = f"{PLANNER_PROMPT}\n\nTool catalog:\n{tool_catalog}"
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages


def _render_plan(plan: dict, observations: list[dict]) -> str:
    lines = [f"Goal: {plan.get('goal', '')}", "Steps:"]
    result_by_id = {
        str(item.get("step", {}).get("id")): item.get("result")
        for item in observations
    }
    for step in plan.get("steps", []):
        lines.append(
            f"- [{step.get('status')}] {step.get('tool')} "
            f"args={step.get('args')} why={step.get('why')}"
        )
        result = result_by_id.get(str(step.get("id")))
        if result is not None:
            lines.append(f"    result: {result}")
    return "\n".join(lines)


def build_summary_messages(
    user_text: str,
    plan: dict,
    observations: list[dict],
) -> list[dict]:
    """Build the summary call messages using the SOUL voice document."""
    system = f"{load_soul()}\n\n{SUMMARY_INSTRUCTION}"
    body = (
        f"User request: {user_text}\n\n"
        f"Plan and results:\n{_render_plan(plan, observations)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": body},
    ]
