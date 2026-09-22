"""LLM usage recording and aggregation.

Recording happens at the single choke point in ``OpenAICompatClient`` and is
best-effort: a database or plumbing failure is logged as a warning and never
affects the request being measured. Aggregation feeds ``GET /api/llm/usage``.
"""

import logging
from datetime import UTC, datetime, timedelta

from src.db import session as db_session
from src.db.database import async_session_factory
from src.llm.budget import llm_budget_status

logger = logging.getLogger("loomcore.llm.usage")


async def record_llm_usage(
    *,
    provider: str,
    base_url: str,
    model: str,
    purpose: str,
    session_id: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    latency_ms: int | None,
    attempts: int,
    status: str,
    error: str | None = None,
) -> None:
    """Persist one LLM usage row; never raise (a failure is only logged)."""
    try:
        async with async_session_factory() as db:
            await db_session.add_llm_usage(
                db,
                provider=provider,
                base_url=base_url,
                model=model,
                purpose=purpose,
                session_id=session_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                attempts=attempts,
                status=status,
                error=error,
            )
    except Exception as exc:  # noqa: BLE001 - recording must never break a call
        logger.warning("Failed to record LLM usage: %s", exc)


def _sum(value: object, calls: int) -> int | None:
    """Coerce a SQL ``SUM``: 0 for an empty window, else None when no samples."""
    if value is None:
        return 0 if calls == 0 else None
    return int(value)


def _avg(value: object, calls: int) -> int | None:
    """Coerce a SQL ``AVG`` to whole milliseconds (None when no latency samples)."""
    if value is None:
        return 0 if calls == 0 else None
    return int(round(float(value)))


def shape_usage_summary(
    *,
    days: int,
    since_iso: str,
    until_iso: str,
    totals: dict,
    by_model: list[dict],
    by_day: list[dict],
    budget: dict,
) -> dict:
    """Assemble the API response from raw aggregation rows (pure, no DB)."""
    total_calls = int(totals.get("calls") or 0)
    return {
        "days": days,
        "since": since_iso,
        "until": until_iso,
        "totals": {
            "calls": total_calls,
            "prompt_tokens": _sum(totals.get("prompt_tokens"), total_calls),
            "completion_tokens": _sum(
                totals.get("completion_tokens"), total_calls
            ),
            "total_tokens": _sum(totals.get("total_tokens"), total_calls),
            "avg_latency_ms": _avg(totals.get("avg_latency_ms"), total_calls),
        },
        "by_model": [
            {
                "provider": row.get("provider") or "",
                "model": row.get("model") or "",
                "calls": int(row.get("calls") or 0),
                "prompt_tokens": _sum(
                    row.get("prompt_tokens"), int(row.get("calls") or 0)
                ),
                "completion_tokens": _sum(
                    row.get("completion_tokens"), int(row.get("calls") or 0)
                ),
                "total_tokens": _sum(
                    row.get("total_tokens"), int(row.get("calls") or 0)
                ),
                "avg_latency_ms": _avg(
                    row.get("avg_latency_ms"), int(row.get("calls") or 0)
                ),
            }
            for row in by_model
        ],
        "by_day": [
            {
                "day": row.get("day") or "",
                "calls": int(row.get("calls") or 0),
                "prompt_tokens": _sum(
                    row.get("prompt_tokens"), int(row.get("calls") or 0)
                ),
                "completion_tokens": _sum(
                    row.get("completion_tokens"), int(row.get("calls") or 0)
                ),
                "total_tokens": _sum(
                    row.get("total_tokens"), int(row.get("calls") or 0)
                ),
            }
            for row in by_day
        ],
        "budget": budget,
    }


async def llm_usage_summary(days: int = 7) -> dict:
    """Read and shape aggregated usage for the last ``days`` days."""
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    since_iso = since.isoformat()
    until_iso = until.isoformat()
    async with async_session_factory() as db:
        totals = await db_session.llm_usage_totals(db, since_iso)
        by_model = await db_session.llm_usage_by_model(db, since_iso)
        by_day = await db_session.llm_usage_by_day(db, since_iso)
    return shape_usage_summary(
        days=days,
        since_iso=since_iso,
        until_iso=until_iso,
        totals=totals,
        by_model=by_model,
        by_day=by_day,
        budget=llm_budget_status(),
    )
