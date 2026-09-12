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

# === Host packages (run on the host, not inside the container) ===

echo "=== Installing WireGuard packages ==="
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update && apt-get install -y wireguard-tools iproute2 qrencode iptables nftables
  echo "Installed via apt"
elif command -v dnf >/dev/null 2>&1; then
  # On RHEL/Rocky, wireguard-tools and qrencode live in EPEL.
  dnf install -y epel-release
  dnf install -y wireguard-tools iproute qrencode iptables-nft nftables
  echo "Installed via dnf"
elif command -v yum >/dev/null 2>&1; then
  yum install -y epel-release
  yum install -y wireguard-tools iproute qrencode iptables nftables
  echo "Installed via yum"
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache wireguard-tools iproute2 qrencode iptables nftables
  echo "Installed via apk"
else
  echo "No supported package manager found. Install wireguard-tools manually."
  exit 1
fi

# WireGuard is in-tree on modern kernels; kmod-wireguard is only needed on
# older RHEL/Rocky kernels.
if command -v modprobe >/dev/null 2>&1; then
  if modprobe wireguard 2>/dev/null; then
    echo "wireguard kernel module available"
  else
    echo "WARNING: 'wireguard' kernel module not loaded. Install the EPEL kmod-wireguard package if wg-quick fails."
  fi
fi

# === Build and start containers ===

echo "=== Building and starting LoomCore ==="
docker compose up -d --build
docker compose ps
echo "LoomCore is available on port 80."
echo ""
echo "Apply DB migrations once: docker compose exec hub alembic upgrade head"
echo "Then configure WireGuard via the wg_setup_server tool in the LoomCore UI/chat."
