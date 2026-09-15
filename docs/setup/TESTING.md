# Testing LoomCore

LoomCore targets Linux servers, so all checks run in a Linux environment:
**WSL2 Ubuntu**, **Linux containers** (Docker Desktop), or a **VDS**. Windows is
only a terminal/editor — there is no Windows-native support.

## Test levels

| Level | Marker | What it checks | Where to run |
|---|---|---|---|
| Unit | — (default) | core, LLM router, manager, LoomWG logic, JSON-RPC protocol | anywhere (WSL2/CI) |
| Integration | `integration` | Postgres reachability | WSL2/Docker/CI |
| E2E | `e2e` | real WireGuard (`wg-quick`) | WSL2/VDS |

## Fast loop (WSL2 Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y wireguard-tools iproute2 qrencode iptables nftables

uv sync --extra dev

# unit tests (no DB, no WireGuard)
pytest -q -m "not integration and not e2e"

# linter
ruff check .
```

The unit run also covers the explicit contracts: `tests/test_contracts.py`
(tool arguments), `tests/test_events.py` (WebSocket event registry) and the
`check_compatibility` cases in `tests/test_plugin_manifest.py` / the schema and
declared-risk cases in `tests/test_plugin_manager.py`.

WSL2 usually has no `python` command (only `python3`); the plugin manager
launches subprocesses via `sys.executable`, so this is fine.

### Postgres (integration)

Easiest: run only the DB in Docker and the core in WSL2.

```bash
docker compose up -d db
export DATABASE_URL="postgresql+asyncpg://loomuser:secret_password@127.0.0.1:5432/loomcore"
alembic upgrade head
LOOMCORE_INTEGRATION=1 pytest -q -m integration
```

### Running the core locally (WSL2)

```bash
export PLUGINS_DIR="$PWD/plugins"
export DATABASE_URL="postgresql+asyncpg://loomuser:secret_password@127.0.0.1:5432/loomcore"
uvicorn src.main:app --reload --port 8000
```

`PLUGINS_DIR` replaces the hardcoded `/app/plugins`, so plugins load outside the
container. For auth, set either `ADMIN_TOKEN` or `ADMIN_PASSWORD_HASH` +
`ADMIN_TOTP_SECRET` + `SESSION_SECRET` (password hash: `python -m src.auth 'password'`).

## E2E WireGuard

The test creates a temporary config directory, then brings the `wgloomtest`
interface up and down with a real `wg-quick`. Requires Linux and root:

```bash
sudo LOOMWG_E2E=1 pytest -q -m e2e
```

`LOOMWG_CONFIG_DIR` overrides the WireGuard config directory (default
`/etc/wireguard`), so a real `/etc` is not touched.

## Frontend

```bash
cd frontend
npm ci
npx tsc --noEmit   # next.config.mjs ignores TS errors during build
npm run build
```

## CI

`.github/workflows/ci.yml`:
- `backend` — `ruff check .` + `pytest` (unit);
- `frontend` — `tsc --noEmit` + `next build`;
- `integration` — Postgres service + `pytest -m integration`.

Real-WireGuard E2E does not run in GitHub-hosted runners — manual only, per the
checklist below.

## VDS E2E checklist

1. `sudo bash deploy/install.sh` (builds/starts the stack; host WireGuard packages are not required — the image ships them).
2. `docker compose exec hub alembic upgrade head`.
3. Configure auth in `.env` (JWT or `ADMIN_TOKEN`) and restart.
4. `wg_install_dependencies` (with admin approval) — loads the kernel module and installs missing container tools.
5. `wg_setup_server` (with admin approval) — creates `wg0.conf`, forwarding, firewall/NAT.
6. `wg_create_client` — create a client.
6. Download the config and QR: `/api/artifacts/<token>/{config,qr}`.
7. Connect a real client (phone/PC) with the config.
8. `wg_list_peers` — confirm the peer is `online` (has a handshake).
9. `wg_remove_peer` — verify peer and client files are removed.

For a full Rocky Linux 10 runbook see [VDS_RUNBOOK.md](VDS_RUNBOOK.md).

## Environment variables for tests

| Variable | Purpose |
|---|---|
| `PLUGINS_DIR` | plugins directory (default `/app/plugins`) |
| `LOOMWG_CONFIG_DIR` | WireGuard config directory (default `/etc/wireguard`) |
| `LOOMWG_SKIP_FIREWALL=1` | skip automatic firewall/NAT in `wg_setup_server` (firewalld hosts) |
| `LOOMCORE_INTEGRATION=1` | enable integration tests (Postgres) |
| `LOOMWG_E2E=1` | enable the real-WireGuard e2e test |
| `DATABASE_URL` | Postgres connection string |
