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
  cp .env.example .env
  chmod 600 .env
  echo "Created $REPO_DIR/.env. Edit it, then rerun:"
  echo "  - POSTGRES_PASSWORD (and an admin login below)"
  echo "  - JWT login: SESSION_SECRET, ADMIN_PASSWORD_HASH, ADMIN_TOTP_SECRET"
  echo "    (hash a password with: python -m src.auth 'your-password')"
  echo "  - Leave ADMIN_TOKEN empty unless you set AUTH_MODE=both for headless API access."
  echo ""
  echo "TLS is required by default (REQUIRE_TLS=true): enable the 443 block in"
  echo "deploy/nginx/default.conf, or set REQUIRE_TLS=false to defer (not recommended)."
  exit 0
fi

# === Build and start containers ===

echo "=== Building and starting the database ==="
docker compose build
docker compose stop hub 2>/dev/null || true
docker compose up -d --wait db

echo "=== Applying database migrations ==="
docker compose run --rm -T hub alembic upgrade head

echo "=== Starting LoomCore ==="
docker compose up -d --wait
docker compose ps

echo "LoomCore stack is running. Access it at https://<your-domain> once the"
echo "nginx 443 block is enabled (REQUIRE_TLS=true; login refuses plain HTTP)."
echo ""
echo "WireGuard is installed by the LoomWG plugin: call wg_install_dependencies"
echo "(loads the kernel module + installs container tools if needed), then configure"
echo "the server with wg_setup_server in the LoomCore UI/chat."
