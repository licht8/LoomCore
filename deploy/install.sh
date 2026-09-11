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

# === Установка пакетов на хосте (выполняется на хосте, не в контейнере) ===

echo "=== Установка WireGuard-пакетов ==="
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update && apt-get install -y wireguard-tools iproute2 qrencode iptables nftables
  echo "Установлено через apt"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y wireguard-tools iproute iptables-nft nftables
  echo "Установлено через dnf"
elif command -v yum >/dev/null 2>&1; then
  yum install -y wireguard-tools iproute iptables nftables
  echo "Установлено через yum"
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache wireguard-tools iproute2 iptables nftables
  echo "Установлено через apk"
else
  echo "Не найден поддерживаемый пакетный менеджер. Установите wireguard-tools вручную."
  exit 1
fi

# === Сборка и запуск Docker-контейнеров ===
echo "=== Сборка и запуск LoomHub ==="
docker compose up -d --build
docker compose ps
echo "LoomHub is available on port 80."
echo ""
echo "Для настройки WireGuard-сервера используйте инструмент wg_setup_server"
echo "в чате LoomHub (или через API /api/chat)."
