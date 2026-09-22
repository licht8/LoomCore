"""OpenAI-compatible provider presets offered in the Settings tab.

Each preset only pre-fills the base URL and a sensible default model; the admin
can override both. ``custom`` intentionally carries no defaults so any
OpenAI-compatible gateway can be used.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    """A selectable LLM provider with optional pre-filled connection details."""

    id: str
    label: str
    default_base_url: str
    default_model: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        id="openai",
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        label="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
    ),
    "groq": ProviderPreset(
        id="groq",
        label="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
    ),
    "custom": ProviderPreset(
        id="custom",
        label="Custom (OpenAI-compatible)",
        default_base_url="",
        default_model="",
    ),
}
