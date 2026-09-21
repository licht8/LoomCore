"""Process-wide daily budget for LLM provider calls.

Per-identity rate limits bound how fast one caller can spend, but a shared or
compromised admin token could still drain the provider balance over time. This
is a coarse global ceiling on the number of LLM calls per UTC day; it defaults
to 1000 calls/day and ``LLM_DAILY_CALL_LIMIT=0`` disables it.
"""

from datetime import UTC, datetime

from src.config import settings
from src.llm.exceptions import LlmBudgetExceeded

# single-threaded event-loop access; no lock needed. Revisit if a second thread is introduced.
_state = {"day": "", "count": 0}


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def reset_llm_budget() -> None:
    """Clear the in-memory counter (tests and process start)."""
    _state["day"] = ""
    _state["count"] = 0


def llm_budget_status() -> dict:
    """Return the current daily budget usage."""
    limit = settings.llm_daily_call_limit
    used = _state["count"] if _state["day"] == _today() else 0
    return {
        "day": _today(),
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0) if limit > 0 else None,
    }


def consume_llm_budget(cost: int = 1) -> dict:
    """Reserve ``cost`` calls from the daily budget and return the new status.

    Raises ``ValueError`` when ``cost`` is less than 1, and ``LlmBudgetExceeded``
    when a limit is configured and the reservation would exceed it. The counter
    rolls over automatically at UTC midnight.
    """
    if cost < 1:
        raise ValueError(f"LLM budget cost must be >= 1, got {cost}")
    limit = settings.llm_daily_call_limit
    today = _today()
    if _state["day"] != today:
        _state["day"] = today
        _state["count"] = 0
    if limit > 0 and _state["count"] + cost > limit:
        raise LlmBudgetExceeded(
            f"Daily LLM call budget exhausted ({_state['count']}/{limit}); "
            "the limit resets at UTC midnight"
        )
    _state["count"] += cost
    used = _state["count"]
    return {
        "day": today,
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0) if limit > 0 else None,
    }
