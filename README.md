# LoomCore

[![CI](https://github.com/licht8/LoomCore/actions/workflows/ci.yml/badge.svg)](https://github.com/licht8/LoomCore/actions/workflows/ci.yml)

**AI-Driven Infrastructure Hub** — an infrastructure orchestrator with an AI assistant.

## Features

- Conversational AI assistant that turns a request into a reviewable plan; a single obvious action runs directly.
- Approval-gated execution — non-safe steps run only after you approve; risky direct actions are confirmed in chat.
- Tamper-evident audit: every mutation is appended to a hash-chained log (`GET /api/audit`, `/api/audit/verify`).
- Auth: static admin token and/or password + TOTP (JWT), TLS-enforced by default, revoked-token persistence and rate limiting.
- Plugin system over MCP (JSON-RPC 2.0, stdio): enable/disable/reload, per-tool timeouts, process-group kill and an allowlist.
- Shipped plugins: **LoomWG** (WireGuard server, peers, client configs/QR) and **LoomOps** (deny-by-default system commands, confined files, processes, services, logs and health scans).
- Runtime LLM configuration (OpenAI-compatible providers) with usage tracking and a daily call budget.
- Web dashboard: Chat, Overview, Tools, Approvals, Plugins, Audit, Usage and Settings.

## Stack

- Python / FastAPI
- PostgreSQL (Docker)
- MCP (Model Context Protocol) for plugins
- OpenAI-compatible LLM providers, chosen at runtime in **Settings**

## Self-hosted on a Linux VDS

```bash
cp .env.example .env
chmod 600 .env
# Fill in POSTGRES_PASSWORD, configure auth (see below), then pick an LLM provider in Settings
nano .env
sudo bash deploy/install.sh          # or: docker compose up -d --build
docker compose exec hub alembic upgrade head
```

After that the UI is available behind Nginx at `http://SERVER_IP/`. The
recommended login is your admin password + TOTP (JWT). The static `ADMIN_TOKEN`
also works, but with the default `REQUIRE_TLS=true` it is accepted only over
HTTPS (or behind a proxy that sets `X-Forwarded-Proto: https`); set
`REQUIRE_TLS=false` only for local/testing HTTP. The backend and PostgreSQL are
not published publicly; Nginx is the only public entrypoint, and it also proxies
the WebSocket.

LoomCore loads plugins from `plugins/<name>/main.py` automatically. A plugin runs
on the same Linux host in a host-networked container and can use the provided
host integrations. `plugins/LoomWG` manages WireGuard (server setup, peers,
client configs/QR).

Detailed instructions: [VDS deploy](docs/setup/DEPLOY.md) and the
[Rocky Linux 10 runbook](docs/setup/VDS_RUNBOOK.md).

## Documentation

- [VDS deploy](docs/setup/DEPLOY.md)
- [Rocky Linux 10 runbook](docs/setup/VDS_RUNBOOK.md)
- [Testing](docs/setup/TESTING.md)
