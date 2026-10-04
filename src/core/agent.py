"""Plan-and-execute agent core.

The LLM never calls tools directly. It proposes a plan, this module validates
it, the user approves it, and :func:`execute_plan` replays it verbatim through
an injected dispatcher. Plan-first is therefore structural, not prompt work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from src.core.contracts import ToolContractError, validate_tool_args
from src.core.security import classify_tool_risk

logger = logging.getLogger("loomcore.agent")

_RiskResolver = Callable[[str], str]

AGENT_PLAN_TOOL = "agent_plan"
REPLY_TOOL = "reply"
MAX_PLAN_STEPS = 12
PLANNER_REASK_LIMIT = 1
VALID_PLAN_MODES = {"direct", "plan"}


class _SafeDispatch(Protocol):
    """Runs one tool call, gated by the plan's approval state."""

    async def __call__(
        self, tool: str, args: dict, *, approved: bool = False
    ) -> dict: ...


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
                            "parallel": {
                                "type": "boolean",
                                "description": (
                                    "Set true only for a read-only step that does "
                                    "not depend on another step's output; such "
                                    "steps may run concurrently. Omit or false when "
                                    "a later step needs an earlier step's result."
                                ),
                            },
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


def _normalize_step(
    index: int, raw: object, risk_of: _RiskResolver | None = None
) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    tool = str(raw.get("tool") or "").strip()
    args = raw.get("args")
    resolve_risk = risk_of or classify_tool_risk
    return {
        "id": str(raw.get("id") or f"s{index}"),
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "why": str(raw.get("why") or "").strip(),
        "risk": resolve_risk(tool),
        "parallel": raw.get("parallel") is True,
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


def build_plan(raw_args: dict, risk_of: _RiskResolver | None = None) -> dict:
    """Normalize a model-proposed plan into the stored plan object."""
    raw_steps = raw_args.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps = [
        _normalize_step(index, raw, risk_of) for index, raw in enumerate(raw_steps, 1)
    ]
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


def next_revision(
    plan: dict, steps: list[dict], risk_of: _RiskResolver | None = None
) -> dict:
    """Return a new revision with replaced steps, re-classified as risk."""
    updated = dict(plan)
    updated["steps"] = [
        _normalize_step(index, raw, risk_of) for index, raw in enumerate(steps, 1)
    ]
    updated["revision"] = int(plan.get("revision", 1)) + 1
    updated["mode"] = "plan"
    updated["status"] = "proposed"
    updated["created_at"] = datetime.now(UTC).isoformat()
    return updated


def validate_plan(
    plan: dict,
    known_tools: set[str],
    tool_schemas: dict[str, dict] | None = None,
) -> list[str]:
    """Return a list of problems; an empty list means the plan is valid.

    When ``tool_schemas`` maps a tool name to its ``inputSchema``, each step must
    supply that tool's required arguments; a missing one is a validation error so
    the planner is re-asked instead of running a call that can only fail.
    """
    errors: list[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["plan has no steps"]
    mode = plan.get("mode")
    if mode is not None and mode not in VALID_PLAN_MODES:
        errors.append(f"invalid mode: {mode}")
    if len(steps) > MAX_PLAN_STEPS:
        errors.append(f"plan has more than {MAX_PLAN_STEPS} steps")
    schemas = tool_schemas or {}
    for step in steps:
        tool = step.get("tool")
        if not tool:
            errors.append("a step is missing a tool name")
        elif tool == AGENT_PLAN_TOOL:
            errors.append("a step may not call the planner")
        elif tool not in known_tools:
            errors.append(f"unknown tool: {tool}")
        args = step.get("args", {})
        if not isinstance(args, dict):
            errors.append(f"arguments for {tool or '?'} are not an object")
        elif tool:
            try:
                validate_tool_args(tool, args, schemas.get(tool))
            except ToolContractError as exc:
                errors.append(str(exc))
    return errors


def plan_requires_approval(plan: dict) -> bool:
    """True when any step is not classified safe."""
    return any(step.get("risk") != "safe" for step in plan.get("steps", []))


def plan_is_direct(plan: dict) -> bool:
    """True for a single-step plan that runs without a user-facing plan card."""
    return plan.get("mode") == "direct" and len(plan.get("steps", [])) == 1


def _is_parallel_step(step: dict) -> bool:
    """A step may run concurrently only when it is safe and marked parallel."""
    return step.get("risk") == "safe" and step.get("parallel") is True


def _plan_runs(steps: list[dict]) -> list[list[dict]]:
    """Group consecutive safe parallel steps; every other step runs alone.

    Only safe steps can join a concurrent run, so a run never contains a step
    that needs approval. Runs preserve plan order.
    """
    runs: list[list[dict]] = []
    current: list[dict] = []
    for step in steps:
        if _is_parallel_step(step):
            current.append(step)
            continue
        if current:
            runs.append(current)
            current = []
        runs.append([step])
    if current:
        runs.append(current)
    return runs


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
    all_steps = plan.get("steps", [])
    if len(all_steps) > max_steps:
        plan["status"] = "failed"
        await emit(
            {
                "type": "plan_step",
                "phase": "failed",
                "step": dict(all_steps[0]),
                "error": f"plan has more than {max_steps} steps",
            }
        )
        return {"plan": plan, "results": []}
    results: list[dict] = []

    for run in _plan_runs(all_steps):
        if len(run) == 1:
            step = run[0]
            if step.get("risk") != "safe" and not approved:
                step["status"] = "blocked"
                plan["status"] = "failed"
                await emit(
                    {
                        "type": "plan_step",
                        "phase": "failed",
                        "step": dict(step),
                        "error": "step requires approval",
                    }
                )
                return {"plan": plan, "results": results}

            step["status"] = "running"
            plan["status"] = "running"
            await emit({"type": "plan_step", "phase": "started", "step": dict(step)})
            try:
                raw = await dispatch(step["tool"], step["args"], approved=approved)
            except Exception as exc:  # noqa: BLE001 - surface any tool failure
                logger.warning(f"Plan step {step.get('id')} failed: {exc}")
                step["status"] = "failed"
                step["error"] = str(exc)
                plan["status"] = "failed"
                await emit(
                    {
                        "type": "plan_step",
                        "phase": "failed",
                        "step": dict(step),
                        "error": str(exc),
                    }
                )
                return {"plan": plan, "results": results}

            step["status"] = "done"
            await emit(
                {
                    "type": "plan_step",
                    "phase": "done",
                    "step": dict(step),
                    "result": raw,
                }
            )
            results.append({"step": step, "result": raw})
            continue

        # Concurrent run: every step is safe and independent.
        plan["status"] = "running"
        for step in run:
            step["status"] = "running"
        for step in run:
            await emit({"type": "plan_step", "phase": "started", "step": dict(step)})
        outcomes = await asyncio.gather(
            *(dispatch(step["tool"], step["args"], approved=approved) for step in run),
            return_exceptions=True,
        )
        for step, outcome in zip(run, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning(f"Plan step {step.get('id')} failed: {outcome}")
                step["status"] = "failed"
                step["error"] = str(outcome)
                plan["status"] = "failed"
                await emit(
                    {
                        "type": "plan_step",
                        "phase": "failed",
                        "step": dict(step),
                        "error": str(outcome),
                    }
                )
                # Keep processing siblings: a successful parallel step must
                # still be reported as done and included in the results.
                continue
            step["status"] = "done"
            await emit(
                {
                    "type": "plan_step",
                    "phase": "done",
                    "step": dict(step),
                    "result": outcome,
                }
            )
            results.append({"step": step, "result": outcome})

        if plan["status"] == "failed":
            return {"plan": plan, "results": results}

    plan["status"] = "done"
    return {"plan": plan, "results": results}
