"""Unit tests for the process-wide daily LLM call budget."""

import pytest

from src.config import settings
from src.llm import budget
from src.llm.exceptions import LlmBudgetExceeded


@pytest.fixture(autouse=True)
def _reset_budget(monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_call_limit", 0)
    budget.reset_llm_budget()
    yield
    budget.reset_llm_budget()


def test_unlimited_limit_never_blocks():
    for _ in range(50):
        budget.consume_llm_budget()
    status = budget.llm_budget_status()
    assert status["used"] == 50
    assert status["remaining"] is None


def test_limit_blocks_when_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_call_limit", 3)
    budget.consume_llm_budget()
    budget.consume_llm_budget(cost=2)
    with pytest.raises(LlmBudgetExceeded):
        budget.consume_llm_budget()
    assert budget.llm_budget_status()["used"] == 3


def test_status_reports_remaining(monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_call_limit", 10)
    budget.consume_llm_budget(cost=4)
    status = budget.llm_budget_status()
    assert status["used"] == 4
    assert status["limit"] == 10
    assert status["remaining"] == 6
    assert status["day"]


def test_day_rollover_resets_counter(monkeypatch):
    monkeypatch.setattr(settings, "llm_daily_call_limit", 2)
    budget.consume_llm_budget(cost=2)
    budget._state["day"] = "1970-01-01"
    assert budget.llm_budget_status()["used"] == 0
    budget.consume_llm_budget()
    assert budget.llm_budget_status()["used"] == 1


def test_zero_cost_is_rejected():
    with pytest.raises(ValueError, match="cost must be >= 1"):
        budget.consume_llm_budget(cost=0)
    assert budget.llm_budget_status()["used"] == 0


def test_negative_cost_is_rejected():
    with pytest.raises(ValueError, match="cost must be >= 1"):
        budget.consume_llm_budget(cost=-1)
    assert budget.llm_budget_status()["used"] == 0
