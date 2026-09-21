"""Central core version.

The app metadata and plugin compatibility checks must agree on the core
version. It is read from the installed package metadata, with a source-tree
fallback so a checkout that is not pip-installed still reports a version.
"""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version

logger = logging.getLogger("loomcore.version")

_FALLBACK_VERSION = "0.0.1"


def _resolve_version() -> str:
    """Return the installed core version, warning when metadata is missing."""
    try:
        return version("loomcore")
    except PackageNotFoundError:
        logger.warning(
            "Core package metadata not found (source checkout or broken install); "
            "using fallback version %s. Plugin compatibility checks use this value.",
            _FALLBACK_VERSION,
        )
        return _FALLBACK_VERSION


CORE_VERSION = _resolve_version()
