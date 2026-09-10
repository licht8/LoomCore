"""Tests for live WireGuard peer registration."""

from loom.system.command import CommandResult
from loom.wireguard.manager import WireGuardManager


class StubRunner:
    """Simple command runner stub for WireGuard manager tests."""

    def __init__(self, outputs=None):
        self.outputs = outputs or {}
        self.calls = []

    def run(self, command, timeout=30):
        self.calls.append((command, timeout))
        key = tuple(command)
        result = self.outputs.get(key)

        if isinstance(result, list):
            return result.pop(0)

        if result is not None:
            return result

        return CommandResult(command=list(command), return_code=0, stdout="", stderr="")


class TestWireGuardManager:
    """Verify peer additions to running interfaces."""

    def test_add_peer_to_interface_success(self):
        """A peer should be added dynamically to the active interface without restarting it."""
        runner = StubRunner(
            {
                ("wg", "show", "interfaces"): CommandResult(
                    command=["wg", "show", "interfaces"],
                    return_code=0,
                    stdout="wg0\n",
                    stderr="",
                ),
                ("wg", "show", "wg0"): CommandResult(
                    command=["wg", "show", "wg0"],
                    return_code=0,
                    stdout="interface: wg0\npeer: Zm9v\n",
                    stderr="",
                ),
                ("wg", "set", "wg0", "peer", "Zm9v", "allowed-ips", "10.66.66.2/32"): CommandResult(
                    command=["wg", "set", "wg0", "peer", "Zm9v", "allowed-ips", "10.66.66.2/32"],
                    return_code=0,
                    stdout="",
                    stderr="",
                ),
                ("wg", "show", "wg0"): CommandResult(
                    command=["wg", "show", "wg0"],
                    return_code=0,
                    stdout="interface: wg0\npeer: Zm9v\nallowed ips: 10.66.66.2/32\n",
                    stderr="",
                ),
            }
        )

        manager = WireGuardManager(runner=runner)
        assert manager.add_peer_to_interface("wg0", "Zm9v", "10.66.66.2") is True

    def test_cidr_prefixes_are_not_duplicated(self):
        """Allocator CIDRs must be passed to wg without another prefix length."""
        runner = StubRunner(
            {
                ("wg", "show", "interfaces"): CommandResult(
                    command=["wg", "show", "interfaces"],
                    return_code=0,
                    stdout="wg0\n",
                    stderr="",
                ),
                ("wg", "show", "wg0"): [
                    CommandResult(
                        command=["wg", "show", "wg0"],
                        return_code=0,
                        stdout="interface: wg0\n",
                        stderr="",
                    ),
                    CommandResult(
                        command=["wg", "show", "wg0"],
                        return_code=0,
                        stdout=(
                            "interface: wg0\n"
                            "peer: Zm9v\n"
                            "allowed ips: 10.66.66.2/32, fd42:42:42::2/128\n"
                        ),
                        stderr="",
                    ),
                ],
                (
                    "wg", "set", "wg0", "peer", "Zm9v", "allowed-ips",
                    "10.66.66.2/32,fd42:42:42::2/128",
                ): CommandResult(
                    command=[
                        "wg", "set", "wg0", "peer", "Zm9v", "allowed-ips",
                        "10.66.66.2/32,fd42:42:42::2/128",
                    ],
                    return_code=0,
                    stdout="",
                    stderr="",
                ),
            }
        )

        manager = WireGuardManager(runner=runner)
        result = manager.add_peer_with_result(
            "wg0",
            "Zm9v",
            "10.66.66.2/32",
            client_ipv6="fd42:42:42::2/128",
        )

        assert result.success is True
        assert ("wg", "set", "wg0", "peer", "Zm9v", "allowed-ips", "10.66.66.2/32,fd42:42:42::2/128") in [
            tuple(command) for command, _ in runner.calls
        ]

    def test_add_peer_to_interface_fails_when_interface_missing(self):
        """The manager must fail safely if wg0 is not running."""
        runner = StubRunner(
            {
                ("wg", "show", "interfaces"): CommandResult(
                    command=["wg", "show", "interfaces"],
                    return_code=0,
                    stdout="",
                    stderr="",
                )
            }
        )

        manager = WireGuardManager(runner=runner)
        assert manager.add_peer_to_interface("wg0", "Zm9v", "10.66.66.2") is False

    def test_add_peer_to_interface_handles_duplicate_peer(self):
        """An existing peer should not be duplicated."""
        runner = StubRunner(
            {
                ("wg", "show", "interfaces"): CommandResult(
                    command=["wg", "show", "interfaces"],
                    return_code=0,
                    stdout="wg0\n",
                    stderr="",
                ),
                ("wg", "show", "wg0"): CommandResult(
                    command=["wg", "show", "wg0"],
                    return_code=0,
                    stdout="interface: wg0\npeer: Zm9v\nallowed ips: 10.66.66.2/32\n",
                    stderr="",
                ),
            }
        )

        manager = WireGuardManager(runner=runner)
        assert manager.add_peer_to_interface("wg0", "Zm9v", "10.66.66.2") is True
