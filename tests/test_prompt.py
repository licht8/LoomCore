"""Unit tests for prompt assembly."""

from src.llm.prompt import (
    DEFAULT_SOUL,
    PLANNER_PROMPT,
    SUMMARY_INSTRUCTION,
    SYSTEM_PROMPT,
    build_messages,
    build_planner_messages,
    build_summary_messages,
    load_soul,
)


def test_build_messages_orders_system_history_user():
    history = [
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "reply"},
    ]

    messages = build_messages("hello", history)

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "previous"}
    assert messages[2] == {"role": "assistant", "content": "reply"}
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert len(messages) == 4


def test_build_messages_without_history():
    messages = build_messages("solo", [])

    assert messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "solo"},
    ]


def test_build_messages_does_not_mutate_history():
    history = [{"role": "user", "content": "kept"}]

    build_messages("hello", history)

    assert history == [{"role": "user", "content": "kept"}]


def test_build_planner_messages_includes_catalog():
    history = [{"role": "assistant", "content": "earlier"}]

    messages = build_planner_messages("do it", history, "- core_health: check")

    assert messages[0]["role"] == "system"
    assert "core_health" in messages[0]["content"]
    assert messages[1] == {"role": "assistant", "content": "earlier"}
    assert messages[-1] == {"role": "user", "content": "do it"}


def test_load_soul_reads_file_and_defaults(tmp_path):
    soul = load_soul()
    assert "Loom" in soul
    assert "voice" in soul.lower()

    missing = load_soul(tmp_path / "nope.md")
    assert missing == DEFAULT_SOUL


def test_build_summary_messages_uses_soul():
    plan = {"goal": "check disk", "steps": [{"id": "s1", "tool": "ops_disk_usage"}]}
    observations = [{"step": {"id": "s1"}, "result": {"content": [], "data": {}}}]

    messages = build_summary_messages("how full is the disk?", plan, observations)

    assert messages[0]["role"] == "system"
    assert "Loom" in messages[0]["content"]
    assert "check disk" in messages[1]["content"]
    assert "how full is the disk?" in messages[1]["content"]


def test_build_planner_messages_carries_soul_and_mode_guidance():
    messages = build_planner_messages("hi", [], "- core_health: check")

    system = messages[0]["content"]
    assert "Loom" in system
    assert "mode='direct'" in system
    assert "mode='plan'" in system


def test_planner_prompt_pushes_state_questions_through_agent_plan():
    lowered = PLANNER_PROMPT.lower()
    assert "agent_plan" in PLANNER_PROMPT
    assert "reply" in PLANNER_PROMPT
    assert "current state" in lowered
    assert "never claim you lack a tool" in lowered
    assert "approval" in lowered


def test_planner_prompt_ends_with_the_act_rule():
    messages = build_planner_messages("list processes", [], "- ops_list_processes: x")

    assert messages[0]["content"].rstrip().endswith(
        "call `agent_plan`; otherwise call `reply`."
    )


def test_summary_instruction_demands_verbatim_numbers():
    assert "exactly" in SUMMARY_INSTRUCTION
    assert "Never change" in SUMMARY_INSTRUCTION
    assert "degraded (0 critical, 1 warning, 0 info)" in SUMMARY_INSTRUCTION


def test_summary_instruction_forbids_pasting_raw_output():
    lowered = SUMMARY_INSTRUCTION.lower()
    assert "in your own words" in lowered
    assert "raw tables" in lowered
    assert "summarize" in lowered


def test_planner_prompt_requires_exact_argument_names():
    lowered = PLANNER_PROMPT.lower()
    assert "exact argument names" in lowered
    assert "plugin" in lowered


def test_planner_prompt_forbids_prefixing_with_the_tool_name():
    lowered = PLANNER_PROMPT.lower()
    assert "never prefix it with" in lowered
    assert "only the user-facing message" in lowered


def test_planner_prompt_forbids_bare_tool_names():
    lowered = PLANNER_PROMPT.lower()
    assert "never output a tool name by itself" in lowered
    assert "call the tool instead" in lowered


def test_load_soul_falls_back_on_invalid_utf8(tmp_path):
    path = tmp_path / "SOUL.md"
    path.write_bytes(b"\xff\xfe\x00")

    assert load_soul(path) == DEFAULT_SOUL


def test_load_soul_uses_default_for_blank_file(tmp_path):
    path = tmp_path / "SOUL.md"
    path.write_text("  \n\t", encoding="utf-8")

    assert load_soul(path) == DEFAULT_SOUL


def test_render_plan_ignores_non_dict_and_none_step_entries():
    plan = {
        "goal": "g",
        "steps": [
            {
                "id": "a",
                "tool": "core_health",
                "status": "done",
                "args": {},
                "why": "w",
            }
        ],
    }
    observations = [
        None,
        {},
        {"step": None, "result": "ignored-result"},
        {"step": {"id": "a"}, "result": "kept-result"},
    ]

    body = build_summary_messages("q", plan, observations)[1]["content"]

    assert "kept-result" in body
    assert "ignored-result" not in body
    assert "None" not in body


def test_render_plan_prefers_first_result_for_duplicate_ids():
    plan = {"goal": "g", "steps": [{"id": "a", "tool": "core_health"}]}
    observations = [
        {"step": {"id": "a"}, "result": "first-result"},
        {"step": {"id": "a"}, "result": "second-result"},
    ]

    body = build_summary_messages("q", plan, observations)[1]["content"]

    assert "first-result" in body
    assert "second-result" not in body


def test_render_plan_truncates_long_result():
    plan = {"goal": "g", "steps": [{"id": "a", "tool": "core_health"}]}
    long_result = "x" * (5 * 2000)
    observations = [{"step": {"id": "a"}, "result": long_result}]

    body = build_summary_messages("q", plan, observations)[1]["content"]

    assert "truncated" in body
    assert body.count("x") < len(long_result)


def test_build_summary_messages_truncates_long_user_text():
    plan = {"goal": "g", "steps": [{"id": "a", "tool": "core_health"}]}
    long_user_text = "y" * 20000

    body = build_summary_messages(long_user_text, plan, [])[1]["content"]

    assert "truncated" in body
    assert body.count("y") < len(long_user_text)


def test_build_planner_messages_trims_long_history():
    history = [{"role": "user", "content": "z" * 10000} for _ in range(3)]
    history.append({"role": "user", "content": "latest"})

    messages = build_planner_messages("now", history, "- core_health: check")

    contents = [message["content"] for message in messages[1:]]
    assert contents[-1] == "now"
    assert contents[-2] == "latest"
    assert len(messages) < 6


def test_summary_instruction_requires_markdown():
    lowered = SUMMARY_INSTRUCTION.lower()
    assert "markdown" in lowered
    assert "bullet" in lowered


def test_planner_prompt_mentions_parallel_steps():
    lowered = PLANNER_PROMPT.lower()
    assert '"parallel": true' in lowered
    assert "read-only" in lowered


def test_build_planner_messages_injects_memory_before_history():
    history = [{"role": "assistant", "content": "earlier"}]

    messages = build_planner_messages(
        "do it", history, "- core_health: check", memory="User prefers UTC."
    )

    assert messages[0]["role"] == "system"
    assert "Tool catalog" in messages[0]["content"]
    assert messages[1]["role"] == "system"
    assert "condensed" in messages[1]["content"]
    assert "User prefers UTC." in messages[1]["content"]
    assert messages[2] == {"role": "assistant", "content": "earlier"}
    assert messages[-1] == {"role": "user", "content": "do it"}


def test_build_planner_messages_is_unchanged_without_memory():
    history = [{"role": "assistant", "content": "earlier"}]

    base = build_planner_messages("do it", history, "- core_health: check")

    assert (
        build_planner_messages(
            "do it", history, "- core_health: check", memory=None
        )
        == base
    )
    assert (
        build_planner_messages(
            "do it", history, "- core_health: check", memory="   "
        )
        == base
    )
    assert all("condensed" not in message["content"] for message in base)
