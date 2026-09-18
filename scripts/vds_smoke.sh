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
  local line trimmed q rest content after after_l
  line="$(grep -m1 -E "^${1}=" .env || true)"
  [[ -z "$line" ]] && return 0
  line="${line#*=}"
  line="${line%$'\r'}"          # strip CR
  trimmed="${line#"${line%%[![:space:]]*}"}"   # ltrim
  if [[ "$trimmed" == \"* || "$trimmed" == \'* ]]; then
    q="${trimmed:0:1}"
    rest="${trimmed:1}"
    if [[ "$rest" == *"$q"* ]]; then
      content="${rest%%"$q"*}"
      after="${rest#*"$q"}"                       # text following the closing quote
      after_l="${after#"${after%%[![:space:]]*}"}" # ltrim
      if [[ -z "${after//[[:space:]]/}" || "$after_l" == \#* ]]; then
        line="$content"                           # unquote a matching "..." / '...' pair
      else
        line="${trimmed%"${trimmed##*[![:space:]]}"}"   # malformed: keep verbatim
      fi
    else
      line="${trimmed%"${trimmed##*[![:space:]]}"}"     # unbalanced: keep verbatim
    fi
  else
    line="${line%%[[:space:]]#*}"                 # strip inline comment (whitespace + #)
    line="${line#"${line%%[![:space:]]*}"}"       # ltrim
    line="${line%"${line##*[![:space:]]}"}"       # rtrim
  fi
  printf '%s\n' "$line"
}

TOKEN="$(env_value ADMIN_TOKEN)"
if [[ "$TOKEN" == *\"* || "$TOKEN" == *\\* ]]; then
  echo "ERROR: ADMIN_TOKEN contains an unsafe character (double quote or backslash)" >&2
  exit 1
fi
PG_USER="$(env_value POSTGRES_USER)"; PG_USER="${PG_USER:-loomuser}"
PG_DB="$(env_value POSTGRES_DB)"; PG_DB="${PG_DB:-loomcore}"

FAIL=0
OUT="$(mktemp)"
AUTH_CFG=""
trap 'rm -f "$OUT" "${AUTH_CFG:-}"' EXIT

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
  AUTH_CFG="$(mktemp)"; chmod 600 "$AUTH_CFG"
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" >"$AUTH_CFG"
  check "GET /api/plugins/status" curl -fsS --config "$AUTH_CFG" "$BASE_URL/api/plugins/status"
  check "GET /api/tools" curl -fsS --config "$AUTH_CFG" "$BASE_URL/api/tools"
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
