"""LLM client exception hierarchy, shared by the client and the budget guard."""


class LlmError(RuntimeError):
    """LLM request failed."""


class LlmTimeoutError(LlmError):
    """LLM did not respond within the configured timeout."""


class LlmBudgetExceeded(LlmError):
    """The process-wide daily LLM call budget is exhausted."""
