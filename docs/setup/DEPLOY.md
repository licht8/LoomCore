# Deploying LoomCore on a VDS

For a Rocky Linux 10 walkthrough (EPEL, firewalld, SELinux), see
[VDS_RUNBOOK.md](VDS_RUNBOOK.md).

## Requirements

- Linux server with Docker Engine and the Docker Compose plugin
- DNS record pointing a domain at the server IP (optional; an IP works too)
- Open ports `80` and `443`
- At least 1 GB RAM; add 1-2 GB swap to build the frontend comfortably

## First run

```bash
git clone https://github.com/licht8/LoomCore.git ~/LoomCore
cd ~/LoomCore
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in at least `POSTGRES_PASSWORD`. The LLM provider is **not** set in `.env`
by default: after the stack is up, open the app's **Settings** tab and pick a
provider (OpenAI, OpenRouter, Groq, DeepSeek, or a custom OpenAI-compatible
endpoint). For admin access prefer **JWT-only** (leave `ADMIN_TOKEN` empty and
set `ADMIN_PASSWORD_HASH`, `ADMIN_TOTP_SECRET`, `SESSION_SECRET`) — see
[Admin authentication](#admin-authentication).

### Step 1: Start the stack

```bash
sudo bash deploy/install.sh
```

The script:
- creates `.env` if missing;
- builds the images and starts Postgres (`docker compose build`, `docker compose up -d --wait db`);
- applies database migrations **before** the hub starts (`docker compose run --rm hub alembic upgrade head`);
- starts the rest of the stack (`docker compose up -d --wait`) and prints the container status.

It does **not** install WireGuard packages: the `hub` image already contains the
userspace tools, and the LoomWG plugin installs/repairs anything missing inside
its container. The only host requirement is kernel WireGuard support (in-tree on
Linux 5.6+; see Step 3 for older kernels).

If `.env` did not exist yet, the script creates it and exits — fill it in and
rerun.

### Step 2: Database migrations

The schema is managed by Alembic. `deploy/install.sh` applies migrations
**before** the hub starts, so a running hub never holds prepared statements
planned against the old schema. If you started the containers manually, run them
yourself (and after every update that adds a migration):

```bash
docker compose run --rm hub alembic upgrade head
```

If the hub is already running when you apply a migration manually (for example
`docker compose exec hub alembic upgrade head`), restart it afterwards so it
drops connections holding pre-migration cached plans: `docker compose restart
hub`.

### Step 3: Configure WireGuard

1. Open the Web UI at `http://SERVER_IP/` and log in with the admin token.
2. Call the `wg_install_dependencies` tool (approve it). It loads the WireGuard
   kernel module and installs any missing userspace tools inside the container.
   If the host kernel has no WireGuard support, the tool returns the host-side
   commands to run (e.g. `dnf install -y kmod-wireguard` on RHEL/Rocky).
3. Call the `wg_setup_server` tool (it asks for admin approval). It creates
   `/etc/wireguard/wg0.conf`, enables IPv4/IPv6 forwarding, and configures
   firewall/NAT.
4. On firewalld hosts (RHEL/Rocky), set `LOOMWG_SKIP_FIREWALL=1` and open the
   port/NAT manually — see [VDS_RUNBOOK.md](VDS_RUNBOOK.md).

## Networking model

`hub`, `frontend`, and `nginx` run with `network_mode: host`, because the
WireGuard interface and firewall/NAT must live in the host network namespace so
external peers can connect.

- `nginx` binds `:80` — the only public port.
- `hub` (API/WS) binds `127.0.0.1:8000`; `frontend` binds `127.0.0.1:3000`.
- Postgres is published on `127.0.0.1:5432` only.

The `hub` container keeps least privilege: only the `NET_ADMIN` and `SYSLOG`
capabilities, `no-new-privileges: true`, and one WireGuard mount:

- `/etc/wireguard:/etc/wireguard:z` (rw) — WireGuard configs (`:z` relabels for SELinux)

`NET_RAW` is **not** granted: `wg`, `ip`, `iptables` and `nft` use `NET_ADMIN`
only, and no tool opens raw sockets. `SYS_MODULE` is **not** granted either:
WireGuard is in-tree on Linux 5.6+ and the LoomWG plugin treats the capability
as optional, telling you to load the module on the host if it is missing. For
LoomOps read-only host observability the `hub` additionally uses `pid: host`,
`SYSLOG`, and read-only mounts of `/run/systemd`, `/var/log`,
`/var/log/journal` and `/run/log/journal` (see
`docs/plugins/LoomOps.md`). A journal directory is used only when it holds
`*.journal` files; enable persistent journald on the host for full history.

**Not mounted:** `/:/host`, `/var/run/docker.sock`, `/sys`, `/lib/modules`, the
host `/etc`, and the host D-Bus socket `/run/dbus/system_bus_socket`; **not
used:** `nsenter`, `chroot`.

**Container user — accepted risk.** The `hub` runs as **root** inside its
container. This is deliberate and documented rather than accidental: Docker's
`cap_add` only populates the capability *bounding* set for a non-root user, so a
non-root hub would have empty `CapPrm`/`CapEff` and could no longer run `wg`,
`wg-quick`, `ip`, `iptables`/`nft`, `sysctl` or read `/dev/kmsg` — WireGuard and
host observability would break. (Verified: `docker run --user 10001 --cap-add
NET_ADMIN …` reports `CapEff: 0000000000000000`, while the same image as root
reports the full set.) The blast radius is bounded by the hardening above —
dropped capabilities and mounts, `no-new-privileges: true`, no Docker socket, no
host D-Bus, no host `/etc`. Removing root entirely needs a privileged-helper or
plugins-as-containers split (future work; not in this image).

`wg_create_peer` stores the client config under `/etc/wireguard/clients/conf` and a
protected QR file under `/etc/wireguard/clients/qr_code`, both with mode `600`;
the private key is never returned to the chat or stored in message history.
`qrencode` is included in the image.

To create a client, only a name is required: the address is allocated from free
addresses in the interface network and the server's public IP is detected
automatically. If you need a domain or the external IP cannot be detected, set
`LOOMWG_ENDPOINT` in `.env` (e.g. `vpn.example.com`) or pass `endpoint` in the
tool call.

`wg_setup_server` detects the OS, the external interface from the default route,
and the available firewall (`firewalld`, `ufw`, `nftables`, `iptables`) **inside
the container** (which shares the host network namespace). For firewalld hosts
the plugin cannot drive the host firewall, so use `LOOMWG_SKIP_FIREWALL=1` and
open UDP/NAT manually.

## LLM provider settings

The provider is chosen at runtime in the app's **Settings** tab and persisted to
`/data/llm.json` (override with `LLM_SETTINGS_FILE`) on the `loomdata` volume, so
it survives restarts and takes effect on the next turn without a restart. The API
key is write-only: the UI shows only a short preview, and it never appears in
responses, logs, or the audit log. Optional `LLM_*` environment variables
pre-seed the first start; leave them unset to boot unconfigured.

A non-streaming LLM request can take a while; the client retries transport/timeout
errors and then returns a clear message. Tune via `.env` (seconds / count):

| Variable | Default |
|---|---|
| `LLM_CONNECT_TIMEOUT` | `15` |
| `LLM_READ_TIMEOUT` | `30` |
| `LLM_WRITE_TIMEOUT` | `30` |
| `LLM_POOL_TIMEOUT` | `15` |
| `LLM_MAX_RETRIES` | `1` |
| `LLM_THINKING_TEMPLATE` | `0` |
| `LLM_DAILY_CALL_LIMIT` | `1000` |

`LLM_DAILY_CALL_LIMIT` is a process-wide daily ceiling on LLM HTTP calls (`0`
disables it). It counts calls, not turns: a turn uses 2 (planner + summary) —
but an all-report plan or an explicit raw-output request skips the summary and
uses 1 — plus 1 per planner re-ask and 1 per transport retry. It resets at UTC
midnight.

These defaults (`LLM_READ_TIMEOUT=30`, `LLM_MAX_RETRIES=1`,
`LLM_PLANNER_MAX_TOKENS=1024`, `LLM_SUMMARY_MAX_TOKENS=512`) are the intended
values; the Settings UI must not silently re-override them back to `90`/`2`.
Set `LLM_PLANNER_MODEL` / `LLM_SUMMARY_MODEL` to a smaller model (empty = main
model) — a small planner is the primary latency lever.

The 30s read timeout is a safety bound: with thinking disabled a full turn is a
few seconds, so a request that hangs this long is a provider stall, not real work.
Raise it via `.env` only if you deliberately re-enable long generations.

If timeouts persist, the provider/model is the bottleneck.

## Admin authentication

`AUTH_MODE` selects which credentials are accepted (`src/core/security.py`):

| `AUTH_MODE` | Accepted credentials |
|---|---|
| `token` | static `ADMIN_TOKEN` only |
| `jwt` | `ADMIN_PASSWORD_HASH` + `ADMIN_TOTP_SECRET` session cookies only |
| `both` | either credential |
| *(empty)* | auto: `jwt` when password+TOTP is configured, else `token` |

1. **JWT cookie (recommended):** set `ADMIN_PASSWORD_HASH`, `ADMIN_TOTP_SECRET`
   and `SESSION_SECRET` together. The UI uses the "Login and TOTP" tab. Generate
   the password hash with:
   ```bash
   python -m src.auth 'your-password'
   ```
2. **Static token:** set a long random `ADMIN_TOKEN` in `.env`. It is passed via
   the `Authorization: Bearer <token>` header (REST) or the
   `loomcore.auth.<base64url>` WebSocket subprotocol. Query-string tokens are
   **not** accepted. In the UI, use the "Token" tab — it is kept in per-tab
   `sessionStorage`, never `localStorage`.

Because auto mode prefers `jwt`, a leftover `ADMIN_TOKEN` can no longer silently
bypass password+TOTP: under `AUTH_MODE=jwt` a valid static token is rejected
(HTTP 403). Set `AUTH_MODE=both` explicitly if you need headless API access
alongside the cookie login, and treat the token as a root credential — a leaked
`ADMIN_TOKEN` grants full admin access regardless of password+TOTP. Omitting
`ADMIN_TOKEN` is the safest configuration. Starting with both configured and no
explicit `AUTH_MODE` logs a warning at startup.

The cookie is `HttpOnly`/`SameSite=Strict`; WebSocket, chat, the tool list and
artifact downloads (`/api/artifacts/...`) all require auth. Never publish tokens
and never leave access unconfigured (unconfigured access fails closed with 503).


## Update

```bash
cd ~/LoomCore
git pull
docker compose build
docker compose up -d --wait db
docker compose run --rm hub alembic upgrade head
docker compose up -d --wait
docker image prune -f
```

## HTTPS

**This is not optional in production.** Admin credentials, the session cookie
and the static admin token all cross the public `:80` listener; without TLS they
are sent in cleartext. `deploy/nginx/default.conf` ships a ready-to-use TLS
server block (commented) and always-on security headers.

The stack is secure by default: `SECURE_COOKIES=true` and `REQUIRE_TLS=true`.
With `REQUIRE_TLS=true` the hub **refuses every admin auth path over plain
HTTP** (HTTP 403) — password login, the session cookie, the `Authorization:
Bearer` static token and the WebSocket handshake — and fails to start if
`SECURE_COOKIES` is disabled, so a fresh install never serves credentials in
cleartext. `SECURE_COOKIES` only marks the cookie; TLS enforcement is
independent and covers the token/WS paths too. To defer TLS, either set
`REQUIRE_TLS=false` (logs a startup warning) or, for local plain-HTTP
development only, also set `SECURE_COOKIES=false`.

Enforcement trusts `X-Forwarded-Proto`, which is only safe because the API
binds to `127.0.0.1:8000` (the image default **and** the compose override), so
only the host-local nginx can reach it and set that header. Verify after deploy:
`ss -tlnp | grep 8000` must show `127.0.0.1:8000`, never `0.0.0.0:8000` — an
off-host client reaching 8000 directly could spoof `X-Forwarded-Proto: https`.
nginx must forward the header on both `/api/` and `/ws/` (it does in the shipped
config).

1. Stop the stack so `:80` is free, then obtain a certificate:
   ```bash
   sudo certbot certonly --standalone -d loom.example.com
   ```
2. Copy the certificate next to the nginx config and mount its directory
   read-only (add to the `nginx` service in `docker-compose.yml`):
   ```yaml
   volumes:
     - ./deploy/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
     - ./deploy/nginx/certs:/etc/nginx/certs:ro
   ```
3. In `default.conf`, uncomment the `listen 443 ssl` server block, set
   `server_name` and the cert paths, and change the `:80` block's `location /`
   to redirect to HTTPS (keep `/.well-known/acme-challenge/` on `:80`).
4. `docker compose up -d nginx`.

The frontend switches to `wss://` automatically. With `ADMIN_TOKEN` empty the
UI uses the JWT cookie, which is `HttpOnly`/`SameSite=Strict` and never touches
browser storage. Revoked sessions are persisted to `LOOMCORE_REVOKED_FILE`
(default `/data/revoked.json` on the `loomdata` volume) so logout survives a
restart; an empty value falls back to in-memory (a warning is logged).

## Backups

PostgreSQL data lives in the `pgdata` Docker volume:

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

`.env` and `backup.sql` must never be committed to Git.

## Container security

| Action | Before | Now |
|---|---|---|
| `wg show/add-peer` | `privileged: true` | `NET_ADMIN` capability |
| `wg-quick up/down` | `privileged` | `NET_ADMIN` (in-tree kernel module); add `SYS_MODULE` only if you need in-container `modprobe` |
| `/etc/wireguard` | via `/:/host` | direct bind mount |
| Package install | `chroot /host` | `hub` image + LoomWG `wg_install_dependencies` (in-container) |
| Firewall/NAT | inside container namespace | host namespace (`network_mode: host`) |
| Full root to the host | ✅ | ❌ removed |
| Docker socket | ✅ | ❌ removed |
| Host D-Bus socket | ✅ | ❌ removed (systemd's private socket via `/run/systemd` is used) |
| Host `/etc` | ✅ (via `/:/host`) | ❌ not mounted (no tool reads `/host/etc`) |
| `/lib/modules` | ✅ | ❌ not mounted (`SYS_MODULE` not granted) |
| `NET_RAW` | ✅ | ❌ removed (unused: no raw sockets) |
| Container user | `root` | `root` — accepted risk; Docker cannot grant caps to a non-root user (see "Container user" above) |
| setuid privilege escalation | allowed | blocked (`no-new-privileges: true`) |
