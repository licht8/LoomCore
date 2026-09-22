# LoomCore

**AI-Driven Infrastructure Hub** — an infrastructure orchestrator with an AI assistant.

## Stack

- Python / FastAPI
- PostgreSQL (Docker)
- MCP (Model Context Protocol) for plugins
- OpenAI-compatible LLM providers, chosen at runtime in **Settings**

## Self-hosted on a Linux VDS

```bash
cp .env.example .env
chmod 600 .env
# Fill in POSTGRES_PASSWORD and ADMIN_TOKEN, then pick an LLM provider in Settings
nano .env
sudo bash deploy/install.sh          # or: docker compose up -d --build
docker compose exec hub alembic upgrade head
```

After that the UI is available at `http://SERVER_IP/`. Log in on the **Token**
tab with `ADMIN_TOKEN`. The backend and PostgreSQL are not published publicly;
Nginx is the only public entrypoint, and it also proxies the WebSocket.

LoomCore loads plugins from `plugins/<name>/main.py` automatically. A plugin runs
on the same Linux host in a host-networked container and can use the provided
host integrations. `plugins/LoomWG` manages WireGuard (server setup, peers,
client configs/QR).

Detailed instructions: [VDS deploy](docs/setup/DEPLOY.md) and the
[Rocky Linux 10 runbook](docs/setup/VDS_RUNBOOK.md).

## Documentation

- [Project rules](RULES.md)
- [VDS deploy](docs/setup/DEPLOY.md)
- [Rocky Linux 10 runbook](docs/setup/VDS_RUNBOOK.md)
- [Testing](docs/setup/TESTING.md)
