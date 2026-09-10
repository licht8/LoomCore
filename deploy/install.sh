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

install -d -m 0750 /opt/loomhub
cd /opt/loomhub

if [[ ! -f .env ]]; then
  cp .env.vds.example .env
  chmod 600 .env
  echo "Created /opt/loomhub/.env. Fill in HETZNER_API_KEY and POSTGRES_PASSWORD, then rerun."
  exit 0
fi

docker compose up -d --build
docker compose ps
echo "LoomHub is available on port 80."
