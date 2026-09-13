#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh"
  exit 1
fi

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required. Install Docker Engine and the Compose plugin first."
  exit 1
}

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required."
  exit 1
fi

# Work from the repo this script lives in (works from any clone path).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ ! -f .env ]]; then
  cp .env.vds.example .env
  chmod 600 .env
  echo "Created $REPO_DIR/.env. Fill in HETZNER_API_KEY, POSTGRES_PASSWORD and ADMIN_TOKEN, then rerun."
  exit 0
fi

# === Build and start containers ===

echo "=== Building and starting LoomCore ==="
docker compose up -d --build
docker compose ps

echo "=== Applying database migrations ==="
docker compose exec -T hub alembic upgrade head

echo "LoomCore is available on port 80."
echo ""
echo "WireGuard is installed by the LoomWG plugin: call wg_install_dependencies"
echo "(loads the kernel module + installs container tools if needed), then configure"
echo "the server with wg_setup_server in the LoomCore UI/chat."
