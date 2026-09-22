"""Unit tests for the runtime LLM provider config store."""

import json
import os
import stat

import pytest

from src.llm import config as config_module
from src.llm.config import (
    LlmConfig,
    default_config,
    get_active,
    load_config,
    mask_api_key,
    resolve_config_path,
    save_config,
    set_active,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    config_module.clear_cache()
    yield
    config_module.clear_cache()


def test_unconfigured_default_when_env_empty(monkeypatch):
    for field in (
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "llm_api_key",
    ):
        monkeypatch.setattr(config_module.settings, field, "")

    config = default_config()
    assert config.configured is False
    assert config.base_url == ""
    assert config.model == ""


def test_default_config_maps_env_values(monkeypatch):
    monkeypatch.setattr(config_module.settings, "llm_provider", "groq")
    monkeypatch.setattr(
        config_module.settings, "llm_base_url", "https://api.groq.com/openai/v1"
    )
    monkeypatch.setattr(config_module.settings, "llm_model", "llama-3.3-70b")
    monkeypatch.setattr(config_module.settings, "llm_api_key", "k")
    monkeypatch.setattr(config_module.settings, "llm_max_retries", 5)
    monkeypatch.setattr(config_module.settings, "llm_enable_thinking", True)
    monkeypatch.setattr(config_module.settings, "llm_thinking_template", True)

    config = default_config()
    assert config.provider == "groq"
    assert config.base_url == "https://api.groq.com/openai/v1"
    assert config.model == "llama-3.3-70b"
    assert config.api_key == "k"
    assert config.max_retries == 5
    assert config.enable_thinking is True
    assert config.thinking_template is True
    assert config.configured is True


def test_load_missing_file_returns_default(tmp_path):
    assert load_config(tmp_path / "missing.json") == default_config()


def test_load_corrupt_file_returns_default(tmp_path, caplog):
    path = tmp_path / "llm.json"
    path.write_text("{not json", encoding="utf-8")

    with caplog.at_level("WARNING", logger="loomcore.llm.config"):
        config = load_config(path)

    assert config == default_config()
    assert "Invalid LLM settings JSON" in caplog.text


def test_load_non_dict_returns_default(tmp_path, caplog):
    path = tmp_path / "llm.json"
    path.write_text("[]", encoding="utf-8")

    with caplog.at_level("WARNING", logger="loomcore.llm.config"):
        config = load_config(path)

    assert config == default_config()
    assert "unexpected shape" in caplog.text


def test_load_ignores_unknown_keys_and_bad_types(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "max_retries": "lots",
                "unknown": 1,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(path)
    assert config.provider == "openai"
    assert config.base_url == "https://api.openai.com/v1"
    assert config.max_retries == default_config().max_retries


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "llm.json"
    original = LlmConfig(
        provider="custom",
        base_url="https://my.gateway/v1",
        model="my-model",
        api_key="secret-key",
        summary_model="small-model",
        enable_thinking=True,
        thinking_template=True,
        connect_timeout=3.0,
        read_timeout=12.0,
        write_timeout=4.0,
        pool_timeout=2.0,
        max_retries=1,
    )

    assert save_config(path, original) is True
    assert load_config(path) == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_saved_file_is_private(tmp_path):
    path = tmp_path / "llm.json"
    assert save_config(path, LlmConfig(model="m")) is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_mask_api_key():
    assert mask_api_key("sk-abcdef") == "…cdef"
    assert mask_api_key("abcd") == "…"
    assert mask_api_key("ab") == "…"
    assert mask_api_key("") == ""


def test_configured_property():
    assert LlmConfig().configured is False
    assert LlmConfig(base_url="https://x/v1").configured is False
    assert LlmConfig(base_url="https://x/v1", model="m").configured is True


def test_resolve_config_path():
    assert resolve_config_path("~/custom.json") == config_module.Path(
        "~/custom.json"
    ).expanduser()
    assert resolve_config_path() == config_module.Path(
        config_module.DEFAULT_SETTINGS_FILE
    )


def test_set_active_updates_cache_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_module.settings, "llm_settings_file", str(tmp_path / "llm.json")
    )
    config = LlmConfig(
        provider="custom",
        base_url="https://my.gateway/v1",
        model="my-model",
    )

    assert get_active().configured is False
    assert set_active(config) is True
    assert get_active() == config

    config_module.clear_cache()
    assert get_active() == config


def test_get_active_reloads_when_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "llm.json"
    monkeypatch.setattr(config_module.settings, "llm_settings_file", str(path))
    save_config(path, LlmConfig(base_url="https://first/v1", model="m1"))
    assert get_active().base_url == "https://first/v1"

    save_config(path, LlmConfig(base_url="https://second/v1", model="m2"))
    assert get_active().base_url == "https://second/v1"
