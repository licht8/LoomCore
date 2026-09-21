"""Parse short plain-text confirmations for direct (single-step) actions.

The phrase lists live in a JSON config file, not in code. A confirmation only
counts when the whole message is a short phrase (at most
``MAX_CONFIRMATION_WORDS`` words) from the relevant list; anything longer or
mixed is ``unclear``, and over-long phrases stored in the file are dropped with
a warning so a dead config entry is visible. If the config is missing or
unreadable the lists are empty, so nothing is ever auto-approved. Only
successful loads are memoized: a failed load is retried on the next call (no
restart needed) and ``clear_phrase_cache()`` forces a re-read after an edit.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

CONFIRMATION_TIMEOUT_SECONDS = 300
MAX_CONFIRMATION_WORDS = 3
DEFAULT_CONFIRMATIONS_PATH = Path(__file__).with_name("confirmations.json")

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def normalize(text: str | None) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    if not text:
        return ""
    lowered = text.casefold()
    stripped = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def confirmations_path(path: str | Path | None = None) -> Path:
    """Resolve the confirmations file: explicit arg, then setting, then default."""
    if path is not None:
        return Path(path)
    configured = (settings.confirmations_file or "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIRMATIONS_PATH


class _PhrasesUnavailable(Exception):
    """The confirmations file could not yield usable phrase lists.

    Module-private and raised (not returned) so ``lru_cache`` never memoizes the
    failure: reading a missing or corrupt file again on the next call is safe
    and lets an operator fix the config without a restart.
    """


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> tuple[frozenset[str], frozenset[str]]:
    path = Path(path_str)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _PhrasesUnavailable(f"confirmations file {path} not found") from exc
    except (OSError, ValueError) as exc:
        raise _PhrasesUnavailable(
            f"could not read confirmations file {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise _PhrasesUnavailable(
            f"confirmations file {path} must contain a JSON object"
        )

    return (
        _phrase_set(data.get("approve"), "approve"),
        _phrase_set(data.get("reject"), "reject"),
    )


def _phrase_set(value: object, label: str) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    phrases = {normalize(item) for item in value if isinstance(item, str)}
    phrases.discard("")
    dropped = sorted(
        phrase for phrase in phrases if len(phrase.split()) > MAX_CONFIRMATION_WORDS
    )
    if dropped:
        logger.warning(
            "Confirmations list %r has %d phrase(s) longer than %d words; "
            "ignoring: %s",
            label,
            len(dropped),
            MAX_CONFIRMATION_WORDS,
            ", ".join(dropped),
        )
    return frozenset(
        phrase for phrase in phrases if len(phrase.split()) <= MAX_CONFIRMATION_WORDS
    )


def clear_phrase_cache() -> None:
    """Drop memoized phrase sets so the next load re-reads the file."""
    _load_cached.cache_clear()


def load_phrases(
    path: str | Path | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (approve, reject) normalized phrase sets.

    A missing/corrupt file yields empty sets and a warning but is not cached, so
    it is retried (and picked up once fixed) on the next call.
    """
    try:
        return _load_cached(str(confirmations_path(path)))
    except _PhrasesUnavailable as exc:
        logger.warning("%s; no phrase will confirm", exc)
        return frozenset(), frozenset()


def parse_confirmation(text: str | None, path: str | Path | None = None) -> str:
    """Classify a short message as ``approve``, ``reject``, or ``unclear``."""
    normalized = normalize(text)
    if not normalized:
        return "unclear"
    words = normalized.split()
    if len(words) > MAX_CONFIRMATION_WORDS:
        return "unclear"
    phrase = " ".join(words)
    approve, reject = load_phrases(path)
    if phrase in approve:
        return "approve"
    if phrase in reject:
        return "reject"
    return "unclear"
