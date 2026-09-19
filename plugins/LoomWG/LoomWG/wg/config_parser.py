"""Shared WireGuard config parsing.

WireGuard ``.conf`` files are INI-like: they consist of ``[Section]`` headers
followed by ``Key = Value`` entries. Keys such as ``AllowedIPs`` and
``Address`` may repeat, so the parser preserves entry order and duplicates
instead of collapsing them into a dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION_RE = re.compile(r"^\[(.+)\]$")


@dataclass
class Section:
    """A single ``[Section]`` block with its ordered entries."""

    name: str
    entries: list[tuple[str, str | None]] = field(default_factory=list)


def parse_sections(text: str) -> list[Section]:
    """Parse config text into ordered sections.

    Blank lines and ``#`` comments are ignored. Entries without ``=`` (rare)
    are kept with a ``None`` value so callers can decide what to do with them.
    """
    sections: list[Section] = []
    current: Section | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match:
            current = Section(name=match.group(1).strip())
            sections.append(current)
            continue
        if current is None:
            continue
        key, separator, value = line.partition("=")
        if separator:
            current.entries.append((key.strip(), value.strip()))
        else:
            current.entries.append((line, None))
    return sections


def sections_named(text: str, name: str) -> list[Section]:
    """Return every section whose name matches ``name`` (case-insensitive)."""
    wanted = name.lower()
    return [section for section in parse_sections(text) if section.name.lower() == wanted]


def entries_values(section: Section, key: str) -> list[str]:
    """Return every value recorded for ``key`` (case-insensitive), in order."""
    wanted = key.lower()
    return [
        value
        for entry_key, value in section.entries
        if value is not None and entry_key.lower() == wanted
    ]
