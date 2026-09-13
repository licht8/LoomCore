"""Unit tests for prompt assembly."""

from src.llm.prompt import (
    DEFAULT_SOUL,
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
