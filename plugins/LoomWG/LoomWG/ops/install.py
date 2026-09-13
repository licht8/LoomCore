"""Backwards-compatible re-exports for the LoomWG install helpers.

The implementation lives in :mod:`LoomWG.ops.dependencies` (in-container
package installation and kernel-module checks) and :mod:`LoomWG.ops.provision`
(server config, firewall and forwarding). This module is kept so existing
imports keep working.
"""

from __future__ import annotations

from .dependencies import (
    dependency_report,
    detect_package_manager,
    ensure_dependencies,
    ensure_kernel_module,
    install_wireguard,
    missing_tools,
)
from .provision import setup_server

__all__ = [
    "dependency_report",
    "detect_package_manager",
    "ensure_dependencies",
    "ensure_kernel_module",
    "install_wireguard",
    "missing_tools",
    "setup_server",
]
