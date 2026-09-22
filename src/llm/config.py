"""Runtime LLM provider configuration.

The active provider is stored as JSON (default ``/data/llm.json``, override with
``LLM_SETTINGS_FILE``) so an admin can switch providers at runtime without a
restart. Reads never fail hard: a missing, corrupt or unreadable file falls back
to the environment-derived default plus a warning, so the core always starts.

The API key lives in this file (written ``0600``) and is never returned by the
API; callers expose it only as a masked preview via :func:`mask_api_key`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import settings

logger = logging.getLogger("loomcore.llm.config")

DEFAULT_SETTINGS_FILE = "/data/llm.json"

NOT_CONFIGURED_MESSAGE = (
    "No LLM provider is configured yet. Open Settings to choose one."
)


@dataclass(frozen=True)
class LlmConfig:
    """The active OpenAI-compatible provider configuration."""

    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    summary_model: str = ""
    enable_thinking: bool = False
    thinking_template: bool = False
    connect_timeout: float = 15.0
    read_timeout: float = 90.0
    write_timeout: float = 30.0
    pool_timeout: float = 15.0
    max_retries: int = 2

    @property
    def configured(self) -> bool:
        """True when a base URL and a model are set (an API key is optional)."""
        return bool(self.base_url and self.model)


def resolve_config_path(configured: str = "") -> Path:
    """Resolve the settings path, honouring the configured override when set."""
    if configured:
        return Path(configured).expanduser()
    return Path(DEFAULT_SETTINGS_FILE)


def default_config() -> LlmConfig:
    """Build the config from the optional ``LLM_*`` environment variables."""
    return LlmConfig(
        provider=settings.llm_provider.strip(),
        base_url=settings.llm_base_url.strip(),
        model=settings.llm_model.strip(),
        api_key=settings.llm_api_key,
        enable_thinking=settings.llm_enable_thinking,
        thinking_template=settings.llm_thinking_template,
        connect_timeout=settings.llm_connect_timeout,
        read_timeout=settings.llm_read_timeout,
        write_timeout=settings.llm_write_timeout,
        pool_timeout=settings.llm_pool_timeout,
        max_retries=settings.llm_max_retries,
    )


def mask_api_key(key: str) -> str:
    """Return a short write-only preview of an API key (never the full value)."""
    if not key:
        return ""
    if len(key) <= 4:
        return "…"
    return f"…{key[-4:]}"


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _as_float(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    try:
        number = float(value)
    except OverflowError:
        # An arbitrarily large JSON integer cannot be represented as a float.
        return fallback
    return number if math.isfinite(number) else fallback


def _as_int(value: object, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value


def load_config(path: Path) -> LlmConfig:
    """Read the persisted config; fall back to the env default on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_config()
    except (UnicodeDecodeError, OSError) as exc:
        logger.warning(
            f"Cannot read LLM settings '{path}' ({exc}); using environment defaults"
        )
        return default_config()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            f"Invalid LLM settings JSON in '{path}': {exc}; using environment defaults"
        )
        return default_config()

    if not isinstance(data, dict):
        logger.warning(
            f"LLM settings '{path}' has an unexpected shape; using environment defaults"
        )
        return default_config()

    base = default_config()
    return LlmConfig(
        provider=_as_str(data.get("provider")),
        base_url=_as_str(data.get("base_url")),
        model=_as_str(data.get("model")),
        api_key=_as_str(data.get("api_key")),
        summary_model=_as_str(data.get("summary_model")),
        enable_thinking=_as_bool(data.get("enable_thinking")),
        thinking_template=_as_bool(data.get("thinking_template")),
        connect_timeout=_as_float(data.get("connect_timeout"), base.connect_timeout),
        read_timeout=_as_float(data.get("read_timeout"), base.read_timeout),
        write_timeout=_as_float(data.get("write_timeout"), base.write_timeout),
        pool_timeout=_as_float(data.get("pool_timeout"), base.pool_timeout),
        max_retries=_as_int(data.get("max_retries"), base.max_retries),
    )


def save_config(path: Path, config: LlmConfig) -> bool:
    """Persist the config atomically with restrictive permissions.

    Returns:
        True — the file was written.
        False — the path is not writable (the change is rejected, not applied).
    """
    payload = json.dumps(asdict(config), ensure_ascii=False, indent=2)
    try:
        _atomic_write_text(path, payload + "\n")
    except OSError as exc:
        logger.warning(f"Cannot write LLM settings '{path}': {exc}")
        return False
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` atomically (temp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


_active: LlmConfig | None = None
_active_signature: tuple[int, int] | None = None
_active_path: Path | None = None

# Guards the stat-then-load and write-then-assign sequences below so concurrent
# threads (e.g. the API and a background caller) cannot interleave them.
_config_lock = threading.RLock()


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def clear_cache() -> None:
    """Drop the cached active config (used by tests and reload paths)."""
    global _active, _active_signature, _active_path
    with _config_lock:
        _active = None
        _active_signature = None
        _active_path = None


def get_active() -> LlmConfig:
    """Return the active config, reloading it when the file changes on disk."""
    global _active, _active_signature, _active_path
    with _config_lock:
        path = resolve_config_path(settings.llm_settings_file)
        signature = _file_signature(path)
        if _active is None or _active_path != path or signature != _active_signature:
            _active = load_config(path)
            _active_path = path
            _active_signature = signature
        return _active


def set_active(config: LlmConfig) -> bool:
    """Apply ``config`` at runtime (hot switch) and persist it."""
    global _active, _active_signature, _active_path
    with _config_lock:
        path = resolve_config_path(settings.llm_settings_file)
        saved = save_config(path, config)
        if not saved:
            return False
        _active = config
        _active_path = path
        _active_signature = _file_signature(path)
        return saved
