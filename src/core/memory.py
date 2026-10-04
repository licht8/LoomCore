"""Conversation memory: compact the older history into a persisted summary.

The planner prompt only keeps the last ``_MAX_HISTORY_CHARS`` of verbatim
history, so in a long session the assistant silently forgets how the
conversation started. This module folds everything older than that tail into a
short factual brief stored on the session (``sessions.memory``) and injects it
ahead of the verbatim tail — the assistant keeps the context without growing the
prompt.

Compaction runs *after* a turn, in the background, and never affects the reply:
every failure is swallowed and the stored memory is left unchanged.
"""

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import session as db_session
from src.llm.prompt import MEMORY_INSTRUCTION, _truncate, load_soul

logger = logging.getLogger("loomcore.memory")

# Verbatim tail kept as-is (mirrors _MAX_HISTORY_CHARS in src/llm/prompt.py).
MEMORY_KEEP_CHARS = 6000
# Only compact once the untrimmed history grows past this; a shorter
# conversation keeps today's behaviour (no extra model call).
MEMORY_COMPACT_AFTER_CHARS = 12000
# Newest messages read when folding (bounds the work on a huge session).
MEMORY_HISTORY_LIMIT = 200
# Ceiling for the rendered compaction prompt body.
MAX_MEMORY_BODY_CHARS = 24000


def select_foldable(
    history: list[dict],
    *,
    keep_chars: int = MEMORY_KEEP_CHARS,
) -> tuple[list[dict], list[dict]]:
    """Split ``history`` (oldest first) into ``(folded, kept)``.

    ``kept`` is the newest suffix that fits in ``keep_chars``; ``folded`` is
    everything before it. Messages are walked newest-first so a single message
    is never split across the boundary (the prompt truncates the last one if it
    alone exceeds the budget).
    """
    if not history:
        return ([], [])
    kept: list[dict] = []
    total = 0
    for message in reversed(history):
        size = len(str(message.get("content") or ""))
        if kept and total + size > keep_chars:
            break
        kept.append(message)
        total += size
    kept.reverse()
    return (history[: len(history) - len(kept)], kept)


def _response_text(response: object) -> str:
    """Return the assistant text of a chat completion, or an empty string."""
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _parse_iso(value: object) -> datetime | None:
    """Parse a stored UTC ISO timestamp, assuming UTC when it is naive."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _is_covered(created_at: object, covered_at: object) -> bool:
    """True when a message is at or before the stored coverage marker."""
    marker = _parse_iso(covered_at)
    moment = _parse_iso(created_at)
    if marker is None or moment is None:
        return False
    return moment <= marker


def _render_history(messages: Iterable[dict]) -> str:
    """Render the messages to fold into a plain ``role: content`` transcript."""
    lines: list[str] = []
    for message in messages:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def build_memory_messages(
    existing_memory: str | None,
    folded: list[dict],
) -> list[dict]:
    """Build the messages for one compaction call.

    The existing brief (if any) and the new messages are rendered into a single
    user turn, so the model rewrites the brief instead of appending to it.
    """
    parts: list[str] = []
    if existing_memory and existing_memory.strip():
        parts.append(f"Existing brief:\n{existing_memory.strip()}")
    parts.append("New messages to fold in:\n" + _render_history(folded))
    body = "\n\n".join(parts)
    return [
        {"role": "system", "content": f"{load_soul()}\n\n{MEMORY_INSTRUCTION}"},
        {"role": "user", "content": _truncate(body, MAX_MEMORY_BODY_CHARS)},
    ]


def _history_dicts(messages: Iterable[object]) -> list[dict]:
    """Keep user/assistant text from stored messages (oldest first)."""
    history: list[dict] = []
    for message in messages:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None) or ""
        if role not in ("user", "assistant") or not content.strip():
            continue
        history.append(
            {
                "role": role,
                "content": content,
                "created_at": getattr(message, "created_at", None),
            }
        )
    return history


async def load_memory(session_db: AsyncSession, session_id: str) -> str | None:
    """Return the stored conversation memory, or ``None`` when there is none."""
    memory, _covered_at = await db_session.get_memory(session_db, session_id)
    text = (memory or "").strip()
    return text or None


async def refresh_memory(
    session_db: AsyncSession,
    session_id: str,
    *,
    config,
    client,
) -> str | None:
    """Fold the older history into the stored memory; return the new memory.

    This function is the single compaction gate: it computes the foldable
    user/assistant char total from the same 200-row history read it already
    performs and returns the current memory unchanged while that total stays at
    or below ``MEMORY_COMPACT_AFTER_CHARS``. It also keeps the current memory
    unchanged when there is nothing new to fold or when the model call fails or
    returns an empty result — a failed compaction must never damage the
    conversation or the turn.
    """
    existing, covered_at = await db_session.get_memory(session_db, session_id)
    messages = await db_session.get_session_history(
        session_db, session_id, limit=MEMORY_HISTORY_LIMIT
    )
    history = _history_dicts(messages)
    total = sum(len(str(message.get("content") or "")) for message in history)
    if total <= MEMORY_COMPACT_AFTER_CHARS:
        return (existing or "").strip() or None
    folded, _kept = select_foldable(history)
    pending = [
        message
        for message in folded
        if not _is_covered(message.get("created_at"), covered_at)
    ]
    if not pending:
        return (existing or "").strip() or None

    marker = pending[-1].get("created_at")
    if marker is None:
        logger.warning("Memory compaction skipped: no coverage marker available")
        return (existing or "").strip() or None

    messages_in = build_memory_messages(existing, pending)
    bound: dict[str, float] = (
        {"read_timeout": config.summary_timeout, "attempts": 1}
        if config.summary_timeout > 0
        else {}
    )
    thinking = False if config.summary_disable_thinking else None
    try:
        response = await client.chat_completion(
            messages_in,
            model=config.summary_model or None,
            max_tokens=config.summary_max_tokens,
            thinking=thinking,
            purpose="memory",
            session_id=session_id,
            **bound,
        )
    except Exception as exc:
        logger.warning(f"Memory compaction call failed ({exc}); keeping memory as is")
        return (existing or "").strip() or None

    text = _response_text(response).strip()
    if not text:
        logger.warning("Memory compaction returned an empty result; keeping memory")
        return (existing or "").strip() or None

    await db_session.set_memory(session_db, session_id, text, str(marker))
    return text
