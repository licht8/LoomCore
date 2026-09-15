"""Central core version.

The app metadata and plugin compatibility checks must agree on the core
version. It is read from the installed package metadata, with a source-tree
fallback so a checkout that is not pip-installed still reports a version.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.0.1"

try:
    CORE_VERSION = version("loomcore")
except PackageNotFoundError:  # pragma: no cover - only when run from source
    CORE_VERSION = _FALLBACK_VERSION
