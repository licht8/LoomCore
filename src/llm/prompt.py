from functools import lru_cache
from pathlib import Path

_MAX_RESULT_CHARS = 2000
_MAX_USER_CHARS = 4000
_MAX_HISTORY_CHARS = 6000
_MAX_PLAN_BODY_CHARS = 16000

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
    "You are Loom, the LoomCore assistant. You always answer by calling exactly "
    "one tool: `agent_plan` to act, or `reply` for plain conversation.\n"
    "Act with `agent_plan` whenever the user asks about the current state of the "
    "system (processes, WireGuard, disks, health, logs, plugins, services) or "
    "wants something done. The answer to a state question is whatever the read "
    "tool returns right now — never answer from memory or from earlier messages "
    "(current state is only what a read tool returns during this turn), and never "
    "claim you lack a tool: the catalog below lists every tool you have.\n"
    "Use `reply` only for greetings, explanations, or a single clarifying "
    "question when the request is genuinely ambiguous.\n"
    "Rules for `agent_plan`:\n"
    "- mode='direct' with exactly one step for a single obvious action.\n"
    "- mode='plan' for multi-step work, when the user explicitly asks for a plan, "
    "or when writing it out helps them correct it.\n"
    "- Use only tool names that appear in the catalog below.\n"
    "- Pass every required argument using the exact argument names shown in the "
    "catalog (for example core_disable_plugin takes `plugin`).\n"
    "- Prefer the fewest steps that achieve the request.\n"
    "- Set \"parallel\": true on read-only steps that do not depend on another "
    "step's output (for example several independent status checks); leave it off "
    "when a later step needs an earlier step's result.\n"
    "- When the plan produces a list (tools, plugins, peers, processes, services), "
    "the final answer must be a Markdown bullet list, never a comma-joined "
    "sentence.\n"
    "- Approval-gated tools are still proposed here; the runtime asks the user for "
    "approval, so never tell the user you cannot do it or that they must do it "
    "themselves.\n"
    "- Never call a real tool directly and never write a tool call as text: no "
    "`[tool ...]`, no `Tool result (...)`, no `<tool_call>`, no raw JSON.\n"
    "- Put only the user-facing message in the `text` argument of `reply`; never "
    "prefix it with `reply`, `agent_plan`, or any other tool name, and never "
    "output a tool name by itself — call the tool instead.\n"
    "Examples:\n"
    "- 'list running processes' -> agent_plan with the ops_list_processes tool.\n"
    "- 'list the WireGuard interfaces' -> agent_plan with the wg_list_interfaces tool.\n"
    "- 'disable the LoomWG plugin' -> agent_plan with the core_disable_plugin tool.\n"
    "- 'why is my server slow?' -> agent_plan with the ops_scan_health tool.\n"
    "- 'check disk and memory' -> agent_plan with two parallel steps "
    "(ops_disk_usage and ops_system_metrics), each marked \"parallel\": true.\n"
    "- 'hi' -> reply.\n"
)

SUMMARY_INSTRUCTION = (
    "Answer the user's request by explaining, in your own words, what the tool "
    "output means — following the voice guide above. Use only the facts in the "
    "tool output below. Always reply in Markdown.\n"
    "- Write short paragraphs. Use `-` bullet lists for any enumeration (tools, "
    "plugins, peers, processes, services) — one item per line, never a "
    "comma-joined wall of text.\n"
    "- Use `**bold**` for labels and keys. Use a short heading when it helps.\n"
    "- Do not paste raw tables, JSON, logs, or verbatim tool output; explain "
    "them instead (a Markdown table is fine only when the data is genuinely "
    "tabular).\n"
    "- Preserve every number and status word exactly as it appears in the "
    "results: counts, online/offline, yes/no (for example "
    "'degraded (0 critical, 1 warning, 0 info)'). Never change, round, or infer "
    "a count or status, and never state a value the results do not contain.\n"
    "- If the output is a list, summarize it (how many items, what they have in "
    "common, the notable names) instead of repeating every row.\n"
    "- Be concrete and readable: a short paragraph, or a tight bullet list, that "
    "explains what happened and what it means — never pad or restate the request."
)

DEFAULT_SOUL = (
    "You are Loom. Speak in the first person, plainly and concisely. "
    "Report results, not mechanics."
)

_SOUL_PATH = Path(__file__).with_name("SOUL.md")


@lru_cache(maxsize=8)
def _read_soul(path: Path) -> str:
    """Read a SOUL document, returning the default when it is unusable.

    A missing, unreadable, non-UTF-8, or blank document yields ``DEFAULT_SOUL``.
    Successful reads are memoised per path.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_SOUL
    return text.strip() or DEFAULT_SOUL


def load_soul(path: str | Path | None = None) -> str:
    """Load the voice document, falling back to a minimal default when unusable.

    Missing, unreadable, non-UTF-8, or blank documents return ``DEFAULT_SOUL``.
    Reads are memoised per path via ``_read_soul``.
    """
    return _read_soul(Path(path) if path else _SOUL_PATH)


def _truncate(text: str, limit: int) -> str:
    """Return ``text`` unchanged, or head and tail joined by a marker."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n… (truncated {len(text) - limit} chars) …\n{text[-tail:]}"


def _trim_history(history: list[dict], max_chars: int) -> list[dict]:
    """Drop the oldest messages while the content budget is exceeded.

    The most recent message is always kept; it is truncated when it alone is
    larger than the budget.
    """
    if not history:
        return history
    trimmed = list(history)
    total = sum(len(str(message.get("content") or "")) for message in trimmed)
    while len(trimmed) > 1 and total > max_chars:
        removed = trimmed.pop(0)
        total -= len(str(removed.get("content") or ""))
    last = trimmed[-1]
    last_content = str(last.get("content") or "")
    bounded = _truncate(last_content, max_chars)
    if bounded != last_content:
        trimmed[-1] = {**last, "content": bounded}
    return trimmed


def build_planner_messages(
    user_text: str,
    history: list[dict],
    tool_catalog: str,
) -> list[dict]:
    """Build planner messages: system (voice + planner + catalog), history, user."""
    system = (
        f"{load_soul()}\n\n{PLANNER_PROMPT}\n\nTool catalog:\n{tool_catalog}\n\n"
        "The user's last message is what you answer now. If it is a question about "
        "current state or a request to act, call `agent_plan`; otherwise call `reply`."
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(_trim_history(history, _MAX_HISTORY_CHARS))
    messages.append({"role": "user", "content": _truncate(user_text, _MAX_USER_CHARS)})
    return messages


def _render_plan(plan: dict, observations: list[dict]) -> str:
    lines = [f"Goal: {plan.get('goal', '')}", "Steps:"]
    result_by_id: dict[str, str] = {}
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        if not isinstance(step, dict):
            continue
        result = item.get("result")
        if result is None:
            continue
        key = str(step.get("id") or f"s{index}")
        if key not in result_by_id:
            result_by_id[key] = _truncate(str(result), _MAX_RESULT_CHARS)
    for index, step in enumerate(plan.get("steps", [])):
        lines.append(
            f"- [{step.get('status')}] {step.get('tool')} "
            f"args={step.get('args')} why={step.get('why')}"
        )
        result = result_by_id.get(str(step.get("id") or f"s{index}"))
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
        f"User request: {_truncate(user_text, _MAX_USER_CHARS)}\n\n"
        f"Plan and results:\n{_render_plan(plan, observations)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _truncate(body, _MAX_PLAN_BODY_CHARS)},
    ]
