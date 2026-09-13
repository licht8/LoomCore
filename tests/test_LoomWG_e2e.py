"""E2E test for the LoomWG plugin: real WireGuard (Linux + root).

Skipped by default. Run with:
    sudo LOOMWG_E2E=1 pytest -m e2e
"""

import os
import shutil
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

INTERFACE = "wgloomtest"
PORT = 51899


def _genkey() -> str:
    result = subprocess.run(
        ["wg", "genkey"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_wg_interface_lifecycle(load_loomwg, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMWG_CONFIG_DIR", str(tmp_path))
    wg = load_loomwg("wireguard")["wireguard"]

    config = tmp_path / f"{INTERFACE}.conf"
    config.write_text(
        "[Interface]\n"
        f"PrivateKey = {_genkey()}\n"
        "Address = 10.211.99.1/24\n"
        f"ListenPort = {PORT}\n",
        encoding="utf-8",
    )

    subprocess.run(["wg-quick", "up", str(config)], check=True, timeout=60)
    try:
        assert INTERFACE in wg.active_interfaces()
        details = wg.read_interface(INTERFACE)
        assert details["listen_port"] == PORT
    finally:
        subprocess.run(["wg-quick", "down", str(config)], check=False, timeout=60)
