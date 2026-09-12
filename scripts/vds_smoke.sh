#!/usr/bin/env bash
# Read-only smoke check for a running LoomCore stack on a VDS.
#
# Usage:  bash scripts/vds_smoke.sh [BASE_URL]
# Default BASE_URL: http://127.0.0.1 (through nginx on :80)
#
# Requires: docker compose, curl, ss. Reads ADMIN_TOKEN / POSTGRES_* from .env.
set -Eeuo pipefail

BASE_URL="${1:-http://127.0.0.1}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in $ROOT_DIR" >&2
  exit 1
fi

env_value() {
  grep -E "^$1=" .env | head -n1 | cut -d= -f2- || true
}

TOKEN="$(env_value ADMIN_TOKEN)"
PG_USER="$(env_value POSTGRES_USER)"; PG_USER="${PG_USER:-loomuser}"
PG_DB="$(env_value POSTGRES_DB)"; PG_DB="${PG_DB:-loomcore}"

FAIL=0
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

check() {
  local name="$1"; shift
  if "$@" >"$OUT" 2>&1; then
    echo "OK   $name"
  else
    echo "FAIL $name"
    sed 's/^/     /' "$OUT"
    FAIL=1
  fi
}

echo "== containers =="
docker compose ps

echo "== migrations =="
check "alembic current" docker compose exec -T hub alembic current
check "db tables" docker compose exec -T db psql -U "$PG_USER" -d "$PG_DB" -c '\dt'

echo "== http =="
check "GET /health" curl -fsS "$BASE_URL/health"
if [[ -n "$TOKEN" ]]; then
  check "GET /api/plugins/status" curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/plugins/status"
  check "GET /api/tools" curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/api/tools"
else
  echo "SKIP admin endpoints: ADMIN_TOKEN is empty in .env"
fi

echo "== ports =="
ss -tlnp 2>/dev/null | grep -E ':(80|3000|8000|5432)\b' || echo "(no expected TCP ports found)"
ss -lunp 2>/dev/null | grep -E ':51820\b' || echo "(no :51820/udp yet — run wg_setup_server, then wg-quick up)"

if [[ "$FAIL" -ne 0 ]]; then
  echo "Smoke checks failed."
  exit 1
fi
echo "All smoke checks passed."
