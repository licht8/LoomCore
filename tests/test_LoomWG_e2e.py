"""E2E test for the LoomWG plugin: real WireGuard (Linux + root).

Skipped by default. Run with:
    sudo LOOMWG_E2E=1 pytest -m e2e
"""

import os
import shutil
import socket
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (
            os.environ.get("LOOMWG_E2E") == "1"
            and sys.platform.startswith("linux")
            and shutil.which("wg")
            and shutil.which("wg-quick")
            and os.geteuid() == 0
        ),
        reason="Set LOOMWG_E2E=1 on Linux with wg-quick and root privileges",
    ),
]


def _interface_name() -> str:
    """A per-run WireGuard interface name (at most 15 characters)."""
    return f"wgloom{os.getpid() % 10_000_000:07d}"


def _free_udp_port() -> int:
    """Pick a free UDP port for the test interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _genkey() -> str:
    result = subprocess.run(
        ["wg", "genkey"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _bring_down(config, interface: str) -> None:
    """Tear the interface down without masking the original failure."""
    try:
        result = subprocess.run(
            ["wg-quick", "down", str(config)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print(f"warning: 'wg-quick down' timed out for {interface}", file=sys.stderr)
        return
    if result.returncode != 0:
        print(
            f"warning: 'wg-quick down' failed for {interface} "
            f"(rc={result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )


def test_wg_interface_lifecycle(load_loomwg, monkeypatch, tmp_path):
    interface = _interface_name()
    port = _free_udp_port()
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    wg = load_loomwg("wireguard")["wireguard"]

    config = tmp_path / f"{interface}.conf"
    config.write_text(
        "[Interface]\n"
        f"PrivateKey = {_genkey()}\n"
        "Address = 10.211.99.1/24\n"
        f"ListenPort = {port}\n",
        encoding="utf-8",
    )

    assert interface not in wg.active_interfaces()
    try:
        subprocess.run(["wg-quick", "up", str(config)], check=True, timeout=60)
        assert interface in wg.active_interfaces()
        details = wg.read_interface(interface)
        assert details["listen_port"] == port
    finally:
        _bring_down(config, interface)
