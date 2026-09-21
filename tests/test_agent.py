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


def test_build_plan_handles_non_mapping_steps():
    plan = agent.build_plan({"goal": "x", "steps": ["core_health", 7, None]})

    assert [step["tool"] for step in plan["steps"]] == ["", "", ""]
    errors = agent.validate_plan(plan, {"core_health"})
    assert any("missing a tool name" in error for error in errors)


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


def test_validate_plan_flags_missing_required_arguments():
    known = {"core_disable_plugin"}
    schemas = {
        "core_disable_plugin": {
            "type": "object",
            "properties": {"plugin": {"type": "string"}},
            "required": ["plugin"],
        }
    }

    missing = agent.validate_plan(
        {"steps": [{"tool": "core_disable_plugin", "args": {}}]}, known, schemas
    )
    assert any("missing required argument(s): plugin" in error for error in missing)

    ok = agent.validate_plan(
        {"steps": [{"tool": "core_disable_plugin", "args": {"plugin": "LoomWG"}}]},
        known,
        schemas,
    )
    assert ok == []

    # Without a schema map the required-argument check is skipped.
    assert agent.validate_plan(
        {"steps": [{"tool": "core_disable_plugin", "args": {}}]}, known
    ) == []


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


def test_mode_clamps_to_direct_for_single_step():
    plan = agent.build_plan({"steps": [{"tool": "core_health", "why": "w"}]})
    assert plan["mode"] == "direct"
    assert agent.plan_is_direct(plan) is True


def test_mode_forced_to_plan_for_multiple_steps():
    plan = agent.build_plan(
        {
            "steps": [
                {"tool": "core_health", "why": "w"},
                {"tool": "ops_disk_usage", "why": "w2"},
            ]
        }
    )
    assert plan["mode"] == "plan"
    assert agent.plan_is_direct(plan) is False


def test_explicit_plan_mode_kept_for_single_step():
    plan = agent.build_plan(
        {"mode": "plan", "steps": [{"tool": "core_health", "why": "w"}]}
    )
    assert plan["mode"] == "plan"
    assert agent.plan_is_direct(plan) is False


def test_explicit_direct_mode_with_many_steps_is_clamped():
    plan = agent.build_plan(
        {
            "mode": "direct",
            "steps": [
                {"tool": "core_health", "why": "w"},
                {"tool": "ops_disk_usage", "why": "w2"},
            ],
        }
    )
    assert plan["mode"] == "plan"
    assert agent.plan_is_direct(plan) is False


def test_next_revision_forces_plan_mode():
    plan = agent.build_plan({"steps": [{"tool": "core_health", "why": "w"}]})
    assert plan["mode"] == "direct"
    revised = agent.next_revision(plan, [{"tool": "core_health", "why": "w"}])
    assert revised["mode"] == "plan"


def test_validate_plan_rejects_invalid_mode():
    known = {"core_health"}
    plan = {"mode": "chatty", "steps": [{"tool": "core_health", "args": {}}]}
    assert any("invalid mode" in error for error in agent.validate_plan(plan, known))


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

    async def dispatch(tool, args, *, approved=False):
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

    async def dispatch(tool, args, *, approved=False):
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
    approvals: list = []

    async def dispatch(tool, args, *, approved=False):
        dispatched.append(tool)
        approvals.append(approved)
        return {"content": [], "data": {}}

    async def emit(event):
        return None

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == ["ops_run_command"]
    assert approvals == [True]
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

    async def dispatch(tool, args, *, approved=False):
        dispatched.append(tool)
        raise RuntimeError("boom")

    async def emit(event):
        return None

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    assert dispatched == ["core_health"]
    assert result["plan"]["status"] == "failed"
    assert result["plan"]["steps"][0]["status"] == "failed"
    assert result["plan"]["steps"][0]["error"] == "boom"


@pytest.mark.asyncio
async def test_execute_plan_fails_when_steps_exceed_max():
    plan = agent.build_plan(
        {
            "goal": "g",
            "steps": [
                {"tool": "core_health", "why": "w1"},
                {"tool": "core_list_plugins", "why": "w2"},
            ],
        }
    )
    plan["status"] = "approved"
    dispatched: list = []
    events: list[dict] = []

    async def dispatch(tool, args, *, approved=False):
        dispatched.append(tool)
        return {"content": [], "data": {}}

    async def emit(event):
        events.append(event)

    result = await agent.execute_plan(plan, dispatch=dispatch, emit=emit, max_steps=1)

    assert dispatched == []
    assert result["results"] == []
    assert result["plan"]["status"] == "failed"
    failed = [event for event in events if event["phase"] == "failed"]
    assert len(failed) == 1
    assert "more than 1 steps" in failed[0]["error"]
    assert failed[0]["step"]["tool"] == "core_health"


@pytest.mark.asyncio
async def test_execute_plan_emits_immutable_step_snapshots():
    plan = agent.build_plan(
        {
            "goal": "g",
            "steps": [
                {"tool": "core_health", "why": "w1"},
                {"tool": "core_list_plugins", "why": "w2"},
            ],
        }
    )
    events: list[dict] = []

    async def dispatch(tool, args, *, approved=False):
        return {"content": [], "data": {"tool": tool}}

    async def emit(event):
        events.append(event)

    await agent.execute_plan(plan, dispatch=dispatch, emit=emit)

    started = [event for event in events if event["phase"] == "started"]
    assert started[0]["step"]["status"] == "running"
    assert started[0]["step"] is not plan["steps"][0]
    assert plan["steps"][0]["status"] == "done"
