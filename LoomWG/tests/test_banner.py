"""Tests for the LoomWG banner rendering in ``loom.cli.common``.

These tests verify:

1. ``show_banner()`` output contains the exact banner art from ``BANNER_TEXT``
   (without Rich color tags).
2. ``BANNER_TEXT`` is a non-empty string with exactly 6 lines.
3. ``_gradient_color()`` interpolates between ``THEME['ACCENT']`` and
   ``THEME['PRIMARY']`` as expected.
"""

import re
from unittest.mock import patch

import pytest

from loom.cli.common import BANNER_TEXT, _gradient_color, show_banner
from loom.cli.common import THEME


def _strip_rich_tags(text: str) -> str:
    """Remove Rich inline markup like ``[#c4b5fd]text[/#c4b5fd]``."""
    return re.sub(r"\[[#\w;]+\]", "", text)


def test_banner_renders_correct_art() -> None:
    """show_banner() output contains the exact banner text from BANNER_TEXT."""
    captured = []

    def fake_print(*args, **kwargs):
        for a in args:
            if isinstance(a, str) and a:
                captured.append(a)
            elif hasattr(a, "__str__"):
                captured.append(str(a))

    with patch("loom.cli.common.console.print", side_effect=fake_print):
        with patch("loom.cli.common.clear_screen"):
            show_banner()

    combined = " ".join(captured)
    plain = _strip_rich_tags(combined)
    # Each line of the banner should appear in the output (ignoring color tags)
    for line in BANNER_TEXT.split("\n"):
        assert line in plain, f"Banner line not found in output: {line!r}"


def test_banner_text_is_configurable() -> None:
    """BANNER_TEXT is a non-empty string with exactly 6 lines."""
    assert isinstance(BANNER_TEXT, str)
    assert len(BANNER_TEXT) > 0
    lines = BANNER_TEXT.split("\n")
    assert len(lines) == 6, f"Expected 6 lines, got {len(lines)}"


def test_gradient_color_interpolates() -> None:
    """_gradient_color(0, N) == THEME['ACCENT'], _gradient_color(N-1, N) == THEME['PRIMARY']."""
    total = 6
    # Start → ACCENT (lavender)
    assert _gradient_color(0, total) == THEME["ACCENT"]
    # End → PRIMARY (deep purple)
    assert _gradient_color(total - 1, total) == THEME["PRIMARY"]

    # Midpoint should be between the two hex values.
    mid = _gradient_color(2, total)
    mid_rgb = tuple(int(mid[i + 1 : i + 3], 16) for i in (0, 2, 4))
    accent_rgb = tuple(int(THEME["ACCENT"][i + 1 : i + 3], 16) for i in (0, 2, 4))
    primary_rgb = tuple(int(THEME["PRIMARY"][i + 1 : i + 3], 16) for i in (0, 2, 4))

    for idx, name in enumerate(("R", "G", "B")):
        assert accent_rgb[idx] >= mid_rgb[idx] >= primary_rgb[idx], (
            f"{name}: midpoint {mid_rgb[idx]} not between accent {accent_rgb[idx]} and "
            f"primary {primary_rgb[idx]}"
        )
