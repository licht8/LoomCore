"""Plan-and-execute agent core.

The LLM never calls tools directly. It proposes a plan, this module validates
it, the user approves it, and :func:`execute_plan` replays it verbatim through
an injected dispatcher. Plan-first is therefore structural, not prompt work.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from src.core.security import classify_tool_risk

logger = logging.getLogger("loomcore.agent")

AGENT_PLAN_TOOL = "agent_plan"
REPLY_TOOL = "reply"
MAX_PLAN_STEPS = 12
PLANNER_REASK_LIMIT = 1
VALID_PLAN_MODES = {"direct", "plan"}

_SafeDispatch = Callable[[str, dict], Awaitable[dict]]
_PlanEmit = Callable[[dict], Awaitable[None]]

AGENT_PLAN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": AGENT_PLAN_TOOL,
        "description": (
            "Act on the user's request: propose a single direct tool call for "
            "one obvious action, or an ordered plan for multi-step work. Use "
            "mode='direct' only when exactly one step is needed. Use this "
            "whenever the user asks about the current state of the system or "
            "wants something done. For plain conversation use `reply` instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["direct", "plan"],
                    "description": (
                        "direct for a single obvious action; plan for "
                        "multi-step work."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": "One line: what the plan achieves.",
                },
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "args": {"type": "object"},
                            "why": {"type": "string"},
                        },
                        "required": ["tool", "why"],
                    },
                },
            },
            "required": ["goal", "steps"],
        },
    },
}

REPLY_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": REPLY_TOOL,
        "description": (
            "Send a plain-language chat reply. Use this ONLY for greetings, "
            "explanations, or a single clarifying question. If the user asks "
            "about the current state of the system or wants an action, you MUST "
            "call `agent_plan` instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The reply to show the user.",
                }
            },
            "required": ["text"],
        },
    },
}

# The planner sees exactly these tools. Forcing a tool call (tool_choice) makes
# an unstructured prose turn impossible: the model must choose `reply` for
# conversation or `agent_plan` to act.
PLANNER_TOOLS: list[dict] = [AGENT_PLAN_SCHEMA, REPLY_SCHEMA]


def _normalize_step(index: int, raw: dict) -> dict:
    tool = str(raw.get("tool") or "").strip()
    args = raw.get("args")
    return {
        "id": str(raw.get("id") or f"s{index}"),
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "why": str(raw.get("why") or "").strip(),
        "risk": classify_tool_risk(tool),
        "status": "pending",
    }


def _effective_mode(declared: object, steps: list[dict]) -> str:
    """Clamp the requested mode: one step is direct unless a plan was asked for.

    Multiple steps always require a plan; a single step runs directly unless it
    is forced into plan mode. This keeps the cheap path the default and makes
    the safety-relevant shape a code decision, not a model preference.
    """
    if str(declared or "").strip().lower() == "plan":
        return "plan"
    if len(steps) != 1:
        return "plan"
    return "direct"


def build_plan(raw_args: dict) -> dict:
    """Normalize a model-proposed plan into the stored plan object."""
    raw_steps = raw_args.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps = [_normalize_step(index, raw) for index, raw in enumerate(raw_steps, 1)]
    return {
        "type": "plan",
        "plan_id": f"pl_{uuid4().hex}",
        "revision": 1,
        "goal": str(raw_args.get("goal") or "").strip(),
        "steps": steps,
        "mode": _effective_mode(raw_args.get("mode"), steps),
        "status": "proposed",
        "created_at": datetime.now(UTC).isoformat(),
    }


def next_revision(plan: dict, steps: list[dict]) -> dict:
    """Return a new revision with replaced steps, re-classified as risk."""
    updated = dict(plan)
    updated["steps"] = [
        _normalize_step(index, raw) for index, raw in enumerate(steps, 1)
    ]
    updated["revision"] = int(plan.get("revision", 1)) + 1
    updated["mode"] = "plan"
    updated["status"] = "proposed"
    updated["created_at"] = datetime.now(UTC).isoformat()
    return updated


def validate_plan(plan: dict, known_tools: set[str]) -> list[str]:
    """Return a list of problems; an empty list means the plan is valid."""
    errors: list[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["plan has no steps"]
    mode = plan.get("mode")
    if mode is not None and mode not in VALID_PLAN_MODES:
        errors.append(f"invalid mode: {mode}")
    if len(steps) > MAX_PLAN_STEPS:
        errors.append(f"plan has more than {MAX_PLAN_STEPS} steps")
    for step in steps:
        tool = step.get("tool")
        if not tool:
            errors.append("a step is missing a tool name")
        elif tool == AGENT_PLAN_TOOL:
            errors.append("a step may not call the planner")
        elif tool not in known_tools:
            errors.append(f"unknown tool: {tool}")
        if not isinstance(step.get("args", {}), dict):
            errors.append(f"arguments for {tool or '?'} are not an object")
    return errors


def plan_requires_approval(plan: dict) -> bool:
    """True when any step is not classified safe."""
    return any(step.get("risk") != "safe" for step in plan.get("steps", []))


def plan_is_direct(plan: dict) -> bool:
    """True for a single-step plan that runs without a user-facing plan card."""
    return plan.get("mode") == "direct" and len(plan.get("steps", [])) == 1


async def execute_plan(
    plan: dict,
    *,
    dispatch: _SafeDispatch,
    emit: _PlanEmit,
    max_steps: int = MAX_PLAN_STEPS,
) -> dict:
    """Replay the plan step by step, exactly as stored.

    ``dispatch(tool, args)`` runs a single tool call; ``emit`` receives a
    progress event per phase. A failed step stops execution. Non-safe steps run
    only when the plan has been approved.
    """
    approved = plan.get("status") == "approved"
    steps = plan.get("steps", [])[:max_steps]
    results: list[dict] = []

    for step in steps:
        if step.get("risk") != "safe" and not approved:
            step["status"] = "blocked"
            plan["status"] = "failed"
            await emit(
                {
                    "type": "plan_step",
                    "phase": "failed",
                    "step": step,
                    "error": "step requires approval",
                }
            )
            return {"plan": plan, "results": results}

        step["status"] = "running"
        plan["status"] = "running"
        await emit({"type": "plan_step", "phase": "started", "step": step})
        try:
            raw = await dispatch(step["tool"], step["args"])
        except Exception as exc:  # noqa: BLE001 - surface any tool failure
            logger.warning(f"Plan step {step.get('id')} failed: {exc}")
            step["status"] = "failed"
            step["error"] = str(exc)
            plan["status"] = "failed"
            await emit(
                {
                    "type": "plan_step",
                    "phase": "failed",
                    "step": step,
                    "error": str(exc),
                }
            )
            return {"plan": plan, "results": results}

        step["status"] = "done"
        await emit(
            {"type": "plan_step", "phase": "done", "step": step, "result": raw}
        )
        results.append({"step": step, "result": raw})

    plan["status"] = "done"
    return {"plan": plan, "results": results}
