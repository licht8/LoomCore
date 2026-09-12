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
cp .env.vds.example .env
chmod 600 .env
```

Generate secrets and edit `.env`:

```bash
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 32   # ADMIN_TOKEN
```

Required values:

| Key | Value |
|---|---|
| `POSTGRES_PASSWORD` | the random password above |
| `ADMIN_TOKEN` | the random admin token above |
| `HETZNER_API_KEY` | your Hetzner inference key |
| `LOOMWG_ENDPOINT` | public IP or domain of the VDS |
| `LOOMWG_SKIP_FIREWALL` | `1` (Rocky uses firewalld) |

Store `ADMIN_TOKEN` somewhere safe — you need it for the UI/API.

## 2. Install host packages and start

```bash
sudo bash deploy/install.sh
```

Manual equivalent (if you prefer):

```bash
sudo dnf install -y epel-release
sudo dnf install -y wireguard-tools iproute qrencode iptables-nft nftables
sudo modprobe wireguard   # in-tree on modern kernels; otherwise: dnf install -y kmod-wireguard
sudo docker compose up -d --build
```

`deploy/install.sh` works from wherever you cloned the repo (it uses its own location).

## 3. Apply migrations

```bash
docker compose exec hub alembic upgrade head
docker compose exec db psql -U loomuser -d loomcore -c '\dt'
```

You should see `users`, `sessions`, `messages`, `alembic_version`.

## 4. Check port layout, SELinux, and logs

```bash
ss -tlnp        # :80 public; 8000/3000/5432 on 127.0.0.1
docker compose ps
docker compose logs --tail=100 hub
getenforce
```

Expected `ss -tlnp` (addresses): `0.0.0.0:80`, `127.0.0.1:8000`, `127.0.0.1:3000`,
`127.0.0.1:5432`.

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

curl -s http://SERVER_IP/health
curl -s -H "Authorization: Bearer $TOKEN" http://SERVER_IP/api/plugins/status
curl -s -H "Authorization: Bearer $TOKEN" http://SERVER_IP/api/tools
curl -s "http://SERVER_IP/api/chat?session_id=test1" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"show wireguard status"}'
```

Expected: `{"status":"ok"}`; a plugin status JSON; a tool list (all `wg_*`
except the internal `wg_get_client_artifact`); and a `tool_call`/text answer.

Open the UI at `http://SERVER_IP/` and log in with the **Token** tab using
`ADMIN_TOKEN`.

## 6. Configure WireGuard + firewalld

In the UI (or via the `/api/chat` flow), call `wg_setup_server` (dangerous —
approve it). Because `LOOMWG_SKIP_FIREWALL=1`, the port/NAT is **not** opened
automatically; do it on the host:

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
- The generated `wg0.conf` still contains `PostUp`/`PostDown` iptables rules; with
  firewalld they may be redundant or overlap. If `wg-quick up` fails because of
  them, open the port via firewalld as above and keep using the firewalld rules.

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
docker compose up -d --build
docker compose exec hub alembic upgrade head
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
- Browser localStorage keys changed (`loomcore_admin_token`, `loomcore_session_id`) — re-enter `ADMIN_TOKEN`.

## 10. If something fails, send me

- `docker compose ps`
- `docker compose logs --tail=200 hub`
- `sudo ss -tlnp; sudo ss -lunp`
- `getenforce; sudo ausearch -m avc -ts recent | tail -50`
- `sudo firewall-cmd --list-all`
- the output of the section 5 curl commands

## 11. Manual QA checklist (real-user walkthrough)

Run this after sections 1-6. It exercises the product the way an end user would.

### 11.1 Login (token mode)
1. Open `http://SERVER_IP/`.
2. Switch to the **Token** tab, paste `ADMIN_TOKEN`, click **Войти**.
3. Expected: chat dashboard appears; sidebar shows the LoomWG plugin; no error banner.
- Wrong token -> login rejected. Empty `ADMIN_TOKEN` -> 503 "Admin access not configured".

### 11.2 Read-only chat (safe tools auto-run)
- `show wireguard status` -> `wg_status` runs automatically and shows a tool result.
- `list peers` -> `wg_list_peers`.

Verify history:
```bash
docker compose exec db psql -U loomuser -d loomcore -c \
  "select role, left(content, 60) from messages order by created_at desc limit 10;"
```

### 11.3 Set up the WireGuard server (approval)
1. Type `set up the wireguard server`.
2. Approve the `wg_setup_server` request.
3. With `LOOMWG_SKIP_FIREWALL=1`, apply the section 6 firewall rules.
4. Verify: `ls -l /etc/wireguard/wg0.conf` and `docker compose exec hub wg show`.

### 11.4 Create a peer/client (approval)
1. Type `create a wireguard client named phone1`.
2. Approve `wg_create_client`.
3. Click the config and QR download buttons (`access_token` is appended automatically).
4. Verify:
```bash
ls -l /etc/wireguard/clients/
docker compose exec hub wg show wg0
```
Expect `phone1.conf`/`phone1.png` with mode `600` and a new peer block.

### 11.5 Connect a real device (true e2e)
1. Install WireGuard on a phone/PC, import the QR (or `.conf`), enable the tunnel.
2. Verify:
```bash
docker compose exec hub wg show wg0
```
Expect a fresh `latest handshake` and non-zero transfer; `list peers` shows `online`.

### 11.6 Delete the peer (approval)
1. Type `remove the peer for phone1` (or paste the public key).
2. Approve `wg_remove_peer`.
3. Verify:
```bash
docker compose exec hub wg show wg0
grep -c 'phone1' /etc/wireguard/wg0.conf || true
ls -l /etc/wireguard/clients/
```
Expect: peer gone, no matching `PublicKey`, client files removed.

### 11.7 Negative and UX checks
- Deny a dangerous tool -> `tool_denied`, no server change.
- Invalid name (`bad name!`) -> clear validation error, no client created.
- Duplicate `phone1` -> "client already exists".
- `http://SERVER_IP/api/tools` without token -> 401/503; with `?access_token=...` -> JSON.
- Spam `/api/chat` 31x/min -> 429.
- Refresh the page -> history persists from Postgres.
- Token mode "logout": clear the `loomcore_admin_token` key in browser `localStorage`.

### 11.8 Evidence to collect
- `docker compose ps`, `docker compose logs --tail=200 hub`
- `sudo ss -tlnp`, `sudo ss -lunp`
- `getenforce`, `sudo ausearch -m avc -ts recent | tail -50`, `sudo firewall-cmd --list-all`
- `docker compose exec hub wg show`
- Screenshots/notes of each step, especially any error.

Watch-outs:
- `wg-quick up` failing due to `PostUp` iptables vs firewalld (section 6).
- Peer not connecting: wrong `LOOMWG_ENDPOINT` or UDP 51820 not open/masqueraded.
- UI labels are Russian; backend tool responses are the source of truth.

## Known limitations

- Only `:80` is public; `8000`/`3000`/`5432` are loopback-only.
- On firewalld hosts, `LOOMWG_SKIP_FIREWALL=1` is required and the firewall is manual.
- HTTPS is not configured by default (add Certbot later).
- The host firewall/NAT runs in the host network namespace (containers use
  `network_mode: host`), which is why WireGuard works but isolation is reduced.
