"""Parse short plain-text confirmations for direct (single-step) actions.

The phrase lists live in a JSON config file, not in code. A confirmation only
counts when the whole message is a short phrase from the relevant list; anything
longer or mixed is ``unclear``. If the config is missing or unreadable the lists
are empty, so nothing is ever auto-approved.
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


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> tuple[frozenset[str], frozenset[str]]:
    path = Path(path_str)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Confirmations file %s not found; no phrase will confirm", path)
        return frozenset(), frozenset()
    except (OSError, ValueError) as exc:
        logger.warning("Could not read confirmations file %s: %s", path, exc)
        return frozenset(), frozenset()

    if not isinstance(data, dict):
        logger.warning("Confirmations file %s must contain a JSON object", path)
        return frozenset(), frozenset()

    return _phrase_set(data.get("approve")), _phrase_set(data.get("reject"))


def _phrase_set(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    phrases = {normalize(item) for item in value if isinstance(item, str)}
    return frozenset(phrase for phrase in phrases if phrase)


def load_phrases(
    path: str | Path | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (approve, reject) normalized phrase sets."""
    return _load_cached(str(confirmations_path(path)))


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
