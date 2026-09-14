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
cp .env.vds.example .env
chmod 600 .env
nano .env
```

Fill in at least `POSTGRES_PASSWORD`, `ADMIN_TOKEN`, and `HETZNER_API_KEY`.

### Step 1: Start the stack

```bash
sudo bash deploy/install.sh
```

The script:
- creates `.env` if missing;
- builds and starts the containers via `docker compose up -d --build`;
- applies database migrations (`alembic upgrade head`);
- prints the container status.

It does **not** install WireGuard packages: the `hub` image already contains the
userspace tools, and the LoomWG plugin installs/repairs anything missing inside
its container. The only host requirement is kernel WireGuard support (in-tree on
Linux 5.6+; see Step 3 for older kernels).

If `.env` did not exist yet, the script creates it and exits — fill it in and
rerun.

### Step 2: Database migrations

The schema is managed by Alembic. `deploy/install.sh` applies migrations
automatically. If you started the containers manually with `docker compose up
-d --build`, run them yourself (and after every update that adds a migration):

```bash
docker compose exec hub alembic upgrade head
```

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

The `hub` container keeps least privilege: only `NET_ADMIN`, `NET_RAW`,
`SYS_MODULE`, and two mounts:

- `/etc/wireguard:/etc/wireguard:z` (rw) — WireGuard configs (`:z` relabels for SELinux)
- `/lib/modules:/lib/modules:ro` — kernel modules

**Not mounted:** `/:/host`, `/var/run/docker.sock`, `/sys`; **not used:**
`nsenter`, `chroot`, `pid: host`.

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

## LLM request timeouts

A non-streaming LLM request can take a while; the client retries transport/timeout
errors and then returns a clear message. Tune via `.env` (seconds / count):

| Variable | Default |
|---|---|
| `HETZNER_CONNECT_TIMEOUT` | `15` |
| `HETZNER_READ_TIMEOUT` | `90` |
| `HETZNER_WRITE_TIMEOUT` | `30` |
| `HETZNER_POOL_TIMEOUT` | `15` |
| `HETZNER_MAX_RETRIES` | `2` |

The 90s read timeout is a safety bound: with thinking disabled a full turn is a
few seconds, so a request that hangs this long is a provider stall, not real work.
Raise it via `.env` only if you deliberately re-enable long generations.

If timeouts persist, the provider/model is the bottleneck.

## Admin authentication

Both credentials are accepted **at the same time** (`src/core/security.py:require_admin_token`):

1. **Bearer token:** set a long random `ADMIN_TOKEN` in `.env`. The token is
   passed via the `Authorization: Bearer <token>` header (REST) or the
   `loomcore.auth.<base64url>` WebSocket subprotocol. Query-string tokens are
   **not** accepted. In the UI, use the "Token" tab on the login page — it is
   stored in browser `localStorage`.
2. **JWT cookie:** set `ADMIN_PASSWORD_HASH`, `ADMIN_TOTP_SECRET`, and
   `SESSION_SECRET` together. The UI uses the "Login and TOTP" tab. Generate the
   password hash with:
   ```bash
   python -m src.auth 'your-password'
   ```

The static token keeps working even when JWT login is configured, so you can
still hand it to another admin. Because a leaked `ADMIN_TOKEN` then grants full
admin access regardless of password+TOTP, leave `ADMIN_TOKEN` empty if you want
JWT-only authentication.

Never publish tokens and never leave access unconfigured. WebSocket, chat, the
tool list, and artifact downloads (`/api/artifacts/...`) all require auth.

## Update

```bash
cd ~/LoomCore
git pull
docker compose up -d --build
docker compose exec hub alembic upgrade head
docker image prune -f
```

## HTTPS

**This is not optional in production.** Admin credentials, the session cookie
and the static admin token all cross the public `:80` listener; without TLS they
are sent in cleartext. `deploy/nginx/default.conf` ships a ready-to-use TLS
server block (commented) and always-on security headers.

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
`localStorage`.

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
| `wg-quick up/down` | `privileged` | `NET_ADMIN` + `SYS_MODULE` |
| `/etc/wireguard` | via `/:/host` | direct bind mount |
| Package install | `chroot /host` | `hub` image + LoomWG `wg_install_dependencies` (in-container) |
| Firewall/NAT | inside container namespace | host namespace (`network_mode: host`) |
| Full root to the host | ✅ | ❌ removed |
| Docker socket | ✅ | ❌ removed |
| Host PID namespace | ✅ | ❌ removed |
