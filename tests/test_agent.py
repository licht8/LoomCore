"""Unit tests for the plan-and-execute agent core."""

import pytest

from src.core import agent


def test_build_plan_normalizes_steps():
    plan = agent.build_plan(
        {
            "goal": "inspect the host",
            "steps": [
                {"tool": "core_health", "why": "baseline"},
                {"tool": "ops_run_command", "args": {"command": "df"}, "why": "disk"},
            ],
        }
    )

    assert plan["type"] == "plan"
    assert plan["plan_id"].startswith("pl_")
    assert plan["revision"] == 1
    assert plan["status"] == "proposed"
    assert plan["goal"] == "inspect the host"
    assert [step["id"] for step in plan["steps"]] == ["s1", "s2"]
    assert plan["steps"][0]["risk"] == "safe"
    assert plan["steps"][1]["risk"] == "dangerous"
    assert all(step["status"] == "pending" for step in plan["steps"])


def test_build_plan_handles_bad_shapes():
    plan = agent.build_plan({"goal": "x", "steps": "nope"})
    assert plan["steps"] == []

    plan = agent.build_plan({"steps": [{"tool": "core_health", "args": "bad", "why": "w"}]})
    assert plan["steps"][0]["args"] == {}


def test_validate_plan_reports_problems():
    known = {"core_health", "wg_add_peer"}

    assert agent.validate_plan({"steps": []}, known) == ["plan has no steps"]

    errors = agent.validate_plan(
        {"steps": [{"tool": "ghost", "args": {}}, {"tool": "", "args": {}}]}, known
    )
    assert any("unknown tool: ghost" in error for error in errors)
    assert any("missing a tool name" in error for error in errors)

    planner_step = agent.validate_plan(
        {"steps": [{"tool": agent.AGENT_PLAN_TOOL, "args": {}}]}, known
    )
    assert any("may not call the planner" in error for error in planner_step)

    too_many = {"steps": [{"tool": "core_health", "args": {}}] * (agent.MAX_PLAN_STEPS + 1)}
    assert any("more than" in error for error in agent.validate_plan(too_many, known))


def test_validate_plan_accepts_known_tools():
    known = {"core_health", "wg_add_peer"}
    plan = {"steps": [{"tool": "core_health", "args": {}}]}
    assert agent.validate_plan(plan, known) == []


def test_plan_requires_approval():
    safe = {"steps": [{"risk": "safe"}]}
    risky = {"steps": [{"risk": "safe"}, {"risk": "moderate"}]}
    assert agent.plan_requires_approval(safe) is False
    assert agent.plan_requires_approval(risky) is True


def test_next_revision_reclassifies():
    plan = agent.build_plan({"goal": "g", "steps": [{"tool": "core_health", "why": "w"}]})
    revised = agent.next_revision(plan, [{"tool": "wg_remove_peer", "why": "cleanup"}])

    assert revised["revision"] == 2
    assert revised["status"] == "proposed"
    assert revised["steps"][0]["risk"] == "dangerous"
    assert revised["steps"][0]["status"] == "pending"
    assert revised["plan_id"] == plan["plan_id"]


@pytest.mark.asyncio
async def test_execute_plan_replays_verbatim():
    plan = agent.build_plan(
        {
            "goal": "g",
            "steps": [
                {"tool": "core_health", "args": {"a": 1}, "why": "w1"},
                {"tool": "ops_disk_usage", "args": {"path": "/"}, "why": "w2"},
            ],
        }
    )
    dispatched: list[tuple] = []
    events: list[dict] = []

    async def dispatch(tool, args):
        dispatched.append((tool, args))
        return {"content": [], "data": {"tool": tool}}

    async def emit(event):
        events.append(event)

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == [("core_health", {"a": 1}), ("ops_disk_usage", {"path": "/"})]
    assert result["plan"]["status"] == "done"
    assert [step["status"] for step in result["plan"]["steps"]] == ["done", "done"]
    assert [event["phase"] for event in events] == ["started", "done", "started", "done"]


@pytest.mark.asyncio
async def test_execute_plan_blocks_unapproved_risky_step():
    plan = agent.build_plan(
        {"goal": "g", "steps": [{"tool": "ops_run_command", "why": "w"}]}
    )
    dispatched: list = []

    async def dispatch(tool, args):
        dispatched.append(tool)
        return {"content": [], "data": {}}

    async def emit(event):
        return None

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == []
    assert result["plan"]["status"] == "failed"
    assert result["plan"]["steps"][0]["status"] == "blocked"


@pytest.mark.asyncio
async def test_execute_plan_runs_approved_risky_step():
    plan = agent.build_plan(
        {"goal": "g", "steps": [{"tool": "ops_run_command", "why": "w"}]}
    )
    plan["status"] = "approved"
    dispatched: list = []

    async def dispatch(tool, args):
        dispatched.append(tool)
        return {"content": [], "data": {}}

    async def emit(event):
        return None

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == ["ops_run_command"]
    assert result["plan"]["status"] == "done"


@pytest.mark.asyncio
async def test_execute_plan_stops_on_failure():
    plan = agent.build_plan(
        {
            "goal": "g",
            "steps": [
                {"tool": "core_health", "why": "w1"},
                {"tool": "core_list_plugins", "why": "w2"},
            ],
        }
    )
    dispatched: list = []

    async def dispatch(tool, args):
        dispatched.append(tool)
        raise RuntimeError("boom")

    async def emit(event):
        return None

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == ["core_health"]
    assert result["plan"]["status"] == "failed"
    assert result["plan"]["steps"][0]["status"] == "failed"
    assert result["plan"]["steps"][0]["error"] == "boom"
