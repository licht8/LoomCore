"""Plugin-layer exception types."""


class PluginPolicyDenied(Exception):
    """Raised when a plugin action is refused by the allowlist policy."""
