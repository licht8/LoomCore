# VDS Runbook — Rocky Linux 10

End-to-end self-test for a single Rocky Linux 10 VDS. You run these commands;
nothing here requires external access.

## 0. Prerequisites

- Rocky Linux 10, root/sudo, `git`.
- Docker Engine + Compose plugin:
  ```bash
  docker --version
  docker compose version
  ```
  If missing, install Docker CE for RHEL and verify `docker compose version` works.

## 1. Get the code and configure `.env`

```bash
git clone https://github.com/licht8/LoomCore.git ~/LoomCore
cd ~/LoomCore
cp .env.example .env
chmod 600 .env
```

Generate secrets and edit `.env`:

```bash
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 32   # SESSION_SECRET (JWT login)
python -m src.auth 'your-password'   # ADMIN_PASSWORD_HASH
```

Required values:

| Key | Value |
|---|---|
| `POSTGRES_PASSWORD` | the random password above |
| `LOOMWG_ENDPOINT` | public IP or domain of the VDS |
| `LOOMWG_SKIP_FIREWALL` | `1` (Rocky uses firewalld) |
| `SESSION_SECRET` | the random session secret above (JWT login) |
| `ADMIN_PASSWORD_HASH` | hash generated above (JWT login) |
| `ADMIN_TOTP_SECRET` | base32 TOTP secret from your authenticator app |

`AUTH_MODE` defaults to auto (JWT when password+TOTP is configured), so leave
`ADMIN_TOKEN` empty unless you set `AUTH_MODE=both` for headless API access.
Enable the nginx 443 block before the first login: `REQUIRE_TLS=true` refuses
plain-HTTP logins by default.

Store the credentials somewhere safe — you need them for the UI/API.

## 2. Start the stack

```bash
sudo bash deploy/install.sh
```

Manual equivalent (if you prefer):

```bash
sudo docker compose build
sudo docker compose up -d --wait db
sudo docker compose run --rm hub alembic upgrade head
sudo docker compose up -d --wait
```

If you apply a migration while the hub is already running (`docker compose exec
hub alembic upgrade head`), restart it afterwards so it reconnects: `docker
compose restart hub`.

No host WireGuard packages are needed: the `hub` image ships the userspace tools
and the LoomWG plugin installs/repairs anything missing inside its container. The
kernel normally loads WireGuard on demand (in-tree since Linux 5.6); on an older
kernel install `kmod-wireguard` on the host (see section 6).

`deploy/install.sh` works from wherever you cloned the repo (it uses its own location).

## 3. Apply migrations

`deploy/install.sh` runs migrations **before** the hub starts (`alembic upgrade
head`). If you started the containers manually, run it yourself:

```bash
docker compose run --rm hub alembic upgrade head
docker compose exec db psql -U loomuser -d loomcore -c '\dt'
```

If you apply a migration manually against an already-running hub, restart it
afterwards (`docker compose restart hub`) so it drops connections whose cached
plans predate the schema change.

You should see `users`, `sessions`, `messages`, `alembic_version`.

### Existing database without `alembic_version`

If the database already has `users`/`sessions`/`messages` but Alembic has no
record (error `DuplicateTableError: relation "users" already exists`), the
schema was created before migrations ran. Pick one:

- **Keep the data** (adopt the existing schema): `docker compose exec hub alembic stamp head`
- **Start clean** (this is a test server):
  ```bash
  docker compose stop hub frontend nginx
  docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP TABLE IF EXISTS messages, sessions, users, alembic_version CASCADE;"'
  docker compose up -d
  docker compose exec hub alembic upgrade head
  ```

## 4. Check port layout, SELinux, and logs

```bash
ss -tlnp        # :80 public; 8000/3000/5432 on 127.0.0.1
docker compose ps
docker compose logs --tail=100 hub
getenforce
```

Expected `ss -tlnp` (addresses): `0.0.0.0:80`, `127.0.0.1:8000`, `127.0.0.1:3000`,
`127.0.0.1:5432`.

`8000` **must** be `127.0.0.1`, never `0.0.0.0`: the TLS check trusts
`X-Forwarded-Proto`, which is only safe while nginx is the sole thing that can
reach the API. If `ss` shows `0.0.0.0:8000`, an off-host client could hit the API
directly and spoof `X-Forwarded-Proto: https`, defeating `REQUIRE_TLS`.

If `docker compose logs hub` shows the plugin failing to write `/etc/wireguard`,
SELinux is likely blocking it:

```bash
sudo ausearch -m avc -ts recent | tail -50
sudo chcon -Rt container_file_t /etc/wireguard   # relabel the mount
```

The compose mount already uses `:z`; relabeling is the fallback.

## 5. Smoke tests (no WireGuard yet)

```bash
cd ~/LoomCore
TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2-)

curl -s https://SERVER_NAME/health
curl -s -H "Authorization: Bearer $TOKEN" https://SERVER_NAME/api/plugins/status
curl -s -H "Authorization: Bearer $TOKEN" https://SERVER_NAME/api/tools
curl -s "https://SERVER_NAME/api/chat?session_id=test1" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"show wireguard status"}'
```

Use `https://` (and enable the nginx 443 block first): with the default
`REQUIRE_TLS=true` the hub refuses every admin auth path over plain HTTP —
password login, the session cookie, the `Authorization` token **and** the
WebSocket handshake — so a plain-HTTP Bearer token is rejected (403) just like a
password. nginx forwards `X-Forwarded-Proto` on both `/api/` and `/ws/`, which
is what lets the hub see the original scheme. The `Authorization` curls above
need `ADMIN_TOKEN` set with `AUTH_MODE=both`; on a JWT-only deployment (the
default) log in through the UI and reuse the session cookie.

Expected: `{"status":"ok"}`; a plugin status JSON; a tool list (the `core_*`
meta-tools, the `wg_*` tools except the internal `wg_get_client_artifact`, and the
`ops_*` tools); and a `tool_call`/text answer.

Open the UI at `https://SERVER_NAME/` and sign in with the **Login and TOTP** tab
(or the **Token** tab if `ADMIN_TOKEN` is configured).

### Plugins, core meta-tools and enable/disable

Both bundled plugins load on startup: `LoomWG` and `LoomOps` (enabled by default).
The assistant can inspect and toggle them through the `core_*` meta-tools
(`core_health`, `core_list_plugins`, `core_plugin_status`, `core_list_tools`,
`core_capabilities`, and the approval-gated `core_enable_plugin` /
`core_disable_plugin` / `core_reload_plugin`). The same lifecycle is exposed on
REST:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  http://SERVER_IP/api/plugins/LoomOps/disable
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  http://SERVER_IP/api/plugins/LoomOps/enable
```

State persists to `.loomcore/plugins.json` under the repo (override with
`PLUGINS_STATE_FILE`), so a disabled plugin stays disabled across restarts.

LoomOps is **deny-by-default**: until you set its environment, `ops_run_command`
runs nothing and `ops_read_file`/`ops_write_file` refuse every path. The Compose
stack passes these through from `.env` (empty = deny all), so edit `.env` and
restart `hub`:

```bash
LOOMOPS_ALLOWED_COMMANDS=uptime,df,ss,ip      # ops_run_command allowlist (empty = deny all)
LOOMOPS_ALLOWED_PATHS=/etc/wireguard          # ops_read_file/ops_write_file roots (empty = deny all)
LOOMOPS_AUDIT_LOG=/app/plugins/LoomOps/tmp/loomops-audit.log
LOOMOPS_COMMAND_TIMEOUT=30
```

Every mutating LoomOps call is admin-approved and written as one JSON line to the
audit log.

### Host observability

To let LoomOps describe the whole host (not just the container), `hub` runs with
`pid: host`, `cap_add: [SYSLOG]`, systemd's private socket (via the read-only
`/run/systemd` mount) and read-only mounts of `/var/log`, `/var/log/journal`
(persistent) and `/run/log/journal` (volatile, current boot) under `/host/...`.
A journal directory is used only when it holds `*.journal` files; enable
persistent journald on the host if `/var/log/journal` is empty
(`Storage=persistent` in `/etc/systemd/journald.conf`, then
`mkdir -p /var/log/journal && systemctl restart systemd-journald`). It does
**not** get `privileged`, the Docker socket,
`NET_RAW`, `SYS_MODULE`, the host D-Bus socket, `SYS_PTRACE` or
`DAC_READ_SEARCH`, and the host `/etc` is not mounted; the only writable host
mount is still `/etc/wireguard`. The container runs as **root** (accepted risk:
Docker cannot grant `cap_add` capabilities to a non-root user, so a non-root hub
would break `wg`/`wg-quick`/`ip`/`iptables` and `/dev/kmsg` access). Tune the
health scan with:

```bash
LOOMOPS_LOG_ROOTS=/host/var/log
LOOMOPS_JOURNAL_DIRS=/host/journal,/host/journal-volatile
LOOMOPS_MAX_LOG_LINES=200
LOOMOPS_CPU_THRESHOLD=80
LOOMOPS_DISK_THRESHOLD=90
LOOMOPS_LOAD_THRESHOLD=2.0
```

Ask the assistant for `ops_scan_health` for a structured report (failed units, OOM
events, high CPU/load, nearly-full disks, recent journal errors). Read-only tools
need no approval; `ops_kill_process` / `ops_systemctl_restart` / `ops_run_command`
/ `ops_write_file` stay approval-gated and audited. On SELinux hosts use
`ausearch`/`chcon` to diagnose blocked mounts — never relabel host `/etc` or
`/var/log` with `:z`. See `docs/plugins/LoomOps.md`.

## 6. Configure WireGuard + firewalld

In the UI (or via the `/api/chat` flow), call `wg_install_dependencies` (approve
it) to load the kernel module and install any missing container tools. If it
reports that the host kernel lacks WireGuard, install `kmod-wireguard` on the
host and retry:

```bash
sudo dnf install -y kmod-wireguard   # only for kernels older than Linux 5.6
sudo modprobe wireguard
```

Then call `wg_setup_server` (dangerous — approve it). Because
`LOOMWG_SKIP_FIREWALL=1`, the port/NAT is **not** opened automatically; do it on
the host:

```bash
sudo firewall-cmd --permanent --add-port=51820/udp
sudo firewall-cmd --permanent --add-masquerade
sudo firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -i wg0 -j ACCEPT
sudo firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -o wg0 -j ACCEPT
sudo firewall-cmd --reload

sudo firewall-cmd --list-all
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
```

Notes:
- Adjust the UDP port if you changed `listen_port` in `wg_setup_server`.
- With `LOOMWG_SKIP_FIREWALL=1` the generated `wg0.conf` contains no
  `PostUp`/`PostDown` hooks (the firewalld rules above provide NAT and
  forwarding), so `wg-quick up` never touches iptables.

## 7. Server / client end-to-end

1. UI: `wg_create_client` with a name.
2. Download the config and QR from the generated `/api/artifacts/<token>/{config,qr}` links.
3. Connect a real client (phone/PC) with the config.
4. Verify on the server:
   ```bash
   docker compose exec hub wg show
   docker compose exec hub wg show wg0
   sudo ss -lunp | grep 51820
   ```
5. In the UI call `wg_list_peers` — the peer should be `online` (fresh handshake).
6. In the UI call `wg_remove_peer` and confirm the peer and its client files are gone.

## 8. Verify message history

```bash
docker compose exec db psql -U loomuser -d loomcore -c \
  "select role, left(content, 60), tool_calls from messages order by created_at desc limit 10;"
```

Approved tool results should now be persisted as `role = tool` rows.

## 9. Update

```bash
cd ~/LoomCore
git pull
docker compose build
docker compose up -d --wait db
docker compose run --rm hub alembic upgrade head
docker compose up -d --wait
```

### One-time migration from the old name (LoomHub -> LoomCore)

Only for a server deployed before the rename. Preserves existing data.

```bash
cd ~/LoomCore
git pull

# stop writers, then rename the database
docker compose stop hub frontend nginx
docker compose exec db psql -U loomuser -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='loomhub' AND pid <> pg_backend_pid();"
docker compose exec db psql -U loomuser -d postgres -c 'ALTER DATABASE loomhub RENAME TO loomcore;'

# set POSTGRES_DB=loomcore in .env, then recreate the stack
docker compose down
sudo bash deploy/install.sh
docker compose exec hub alembic upgrade head
```

Notes:
- The Postgres volume is unchanged, so data is preserved.
- Container names change (`loom_hub` -> `loom_core`); `docker compose down` removes the old ones.
- Browser storage changed (`loomcore_admin_token` now lives in per-tab `sessionStorage`) — re-enter `ADMIN_TOKEN` or sign in again.

## 10. If something fails, send me

- `docker compose ps`
- `docker compose logs --tail=200 hub`
- `sudo ss -tlnp; sudo ss -lunp`
- `getenforce; sudo ausearch -m avc -ts recent | tail -50`
- `sudo firewall-cmd --list-all`
- the output of the section 5 curl commands

## 11. Manual QA checklist (real-user walkthrough)

Run this after sections 1-6. It exercises the product the way an end user would.

### 11.1 Login (JWT by default)
1. Open `https://SERVER_NAME/`.
2. Use the **Login and TOTP** tab: username, password, authenticator code.
3. Expected: chat dashboard appears; sidebar shows the LoomWG plugin; no error banner.
- Wrong credentials -> login rejected (401). Plain HTTP -> 403 "TLS is required".
- Token mode only: with `ADMIN_TOKEN` set and `AUTH_MODE=both`, the **Token** tab
  works; under `AUTH_MODE=jwt` a static token is rejected (403). Nothing
  configured -> 503 "Admin access is not configured".

### 11.2 Read-only chat (direct actions auto-run)
- `show wireguard status` is a single obvious read-only action, so it runs as a
  **direct** step: no plan card, just a tool card with the result.
- `list peers` -> `wg_list_peers`, same direct auto-run.
- Greetings, questions, and vague requests get plain conversation: the assistant
  replies in prose or asks one clarifying question. It never calls a tool without
  first making a (direct or plan) decision.

Verify history:
```bash
docker compose exec db psql -U loomuser -d loomcore -c \
  "select role, left(content, 60) from messages order by created_at desc limit 10;"
```

### 11.3 Set up the WireGuard server (plan approval)
1. Type `install wireguard prerequisites` -> the assistant asks
   `... Reply yes to proceed.` in plain text (single dangerous step, direct mode).
   Reply `yes` (or `да`) to run `wg_install_dependencies`.
2. Type `set up the wireguard server`.
3. Reply `yes` to the confirmation (or **Approve** if the assistant bundled it into
   a multi-step plan card) to run `wg_setup_server`.
4. With `LOOMWG_SKIP_FIREWALL=1`, apply the section 6 firewall rules.
5. Verify: `ls -l /etc/wireguard/wg0.conf` and `docker compose exec hub wg show`.

### 11.4 Create a peer/client (plan approval)
1. Type `create a wireguard client named phone1`.
2. Reply `yes` to the confirmation (or **Approve** on the plan card) to run
   `wg_create_client`.
3. Click the config and QR download buttons (the token is sent in the `Authorization` header).
4. Verify:
```bash
ls -l /etc/wireguard/clients/conf /etc/wireguard/clients/qr_code
docker compose exec hub wg show wg0
```
Expect `conf/phone1.conf`/`qr_code/phone1.png` with mode `600` and a new peer block.

### 11.5 Connect a real device (true e2e)
1. Install WireGuard on a phone/PC, import the QR (or `.conf`), enable the tunnel.
2. Verify:
```bash
docker compose exec hub wg show wg0
```
Expect a fresh `latest handshake` and non-zero transfer; `list peers` shows `online`.

### 11.6 Delete the peer (plan approval)
1. Type `remove the peer for phone1` (or paste the public key).
2. Reply `yes` to the confirmation (or **Approve** on the plan card) to run
   `wg_remove_peer`.
3. Verify:
```bash
docker compose exec hub wg show wg0
grep -c 'phone1' /etc/wireguard/wg0.conf || true
ls -l /etc/wireguard/clients/conf /etc/wireguard/clients/qr_code
```
Expect: peer gone, no matching `PublicKey`, client files removed.

### 11.7 Negative and UX checks
- Ask a vague request (e.g. `fix the server`) -> a plain-language clarifying question, no plan and no tool.
- Direct dangerous action -> the assistant asks `... Reply yes to proceed.`; a short `yes`/`да` runs it, `no`/`нет` cancels and clears the pending action. A longer reply (e.g. `да, но сначала логи`) is treated as unclear (one re-ask, then it is handled as a new message).
- A direct confirmation left unanswered for over 5 minutes -> the next message is treated as a new request.
- Reject a multi-step plan card -> `plan_done` with status `rejected`, no server change.
- Edit a plan's steps in the plan card before approving -> the card updates (`revision` +1, forced back to plan mode) and only the edited steps run.
- Send a message, then click the rotating submit button (pause) -> `cancelled`, the running step resolves, and the UI returns to idle.
- A tool that fails (e.g. `wg_interface_status` on a down interface) -> `tool_response` with `status: error`; for a plan, it stops with status `failed` and the card shows the error.
- While a request is running, sending another message -> `busy` (no overlapping runs).
- Invalid name (`bad name!`) -> clear validation error, no client created.
- Duplicate `phone1` -> "client already exists".
- `http://SERVER_IP/api/tools` without token -> 401/503; with `Authorization: Bearer ...` -> JSON. A `?access_token=...` query is ignored.
- Spam `/api/chat` 31x/min -> 429.
- Refresh the page -> the whole conversation (messages, plan cards, tool cards with status/artifacts, errors) is restored from Postgres.
- Click "Clear conversation" -> confirm; the feed empties and the assistant's stored memory is deleted (no `alembic` needed).
- Reload while a plan is awaiting approval -> the plan card is restored in its `proposed` state.
- Token mode "logout": clear the `loomcore_admin_token` key in browser `sessionStorage`.

### 11.8 Evidence to collect
- `docker compose ps`, `docker compose logs --tail=200 hub`
- `sudo ss -tlnp`, `sudo ss -lunp`
- `getenforce`, `sudo ausearch -m avc -ts recent | tail -50`, `sudo firewall-cmd --list-all`
- `docker compose exec hub wg show`
- Screenshots/notes of each step, especially any error.

Watch-outs:
- `wg-quick up` failing because the UDP port/NAT rules are missing (section 6).
- Peer not connecting: wrong `LOOMWG_ENDPOINT` or UDP 51820 not open/masqueraded.
- UI labels are Russian; backend tool responses are the source of truth.

## Known limitations

- Only `:80` is public; `8000`/`3000`/`5432` are loopback-only.
- On firewalld hosts, `LOOMWG_SKIP_FIREWALL=1` is required and the firewall is manual.
- HTTPS is not configured by default (add Certbot later).
- The host firewall/NAT runs in the host network namespace (containers use
  `network_mode: host`), which is why WireGuard works but isolation is reduced.
