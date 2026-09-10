#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log() {
  printf '%b\n' "$1"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    log "${YELLOW}LoomWG setup requires root privileges. Re-running with sudo...${NC}"
    exec sudo bash "$0" "$@"
  fi
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-0}"
    return
  fi

  log "${RED}Unsupported system: /etc/os-release not found.${NC}"
  exit 1
}

fix_centos_stream_repo_once() {
  if [[ "${OS_ID:-}" != "centos" ]]; then
    return 0
  fi

  local marker="/etc/yum.repos.d/.loomwg-centos-mirror-fixed"
  if [[ -f "$marker" ]]; then
    return 0
  fi

  local version="${OS_VERSION:-}"
  if [[ -z "$version" ]]; then
    return 0
  fi

  for repo_file in \
    /etc/yum.repos.d/centos.repo \
    /etc/yum.repos.d/centos-stream.repo \
    /etc/yum.repos.d/CentOS-Stream.repo
  do
    if [[ ! -f "$repo_file" ]]; then
      continue
    fi

    cp -a "$repo_file" "${repo_file}.loomwg.bak" 2>/dev/null || true
    sed -i "s|^metalink=.*centos-baseos-\$stream.*|baseurl=https://mirror.stream.centos.org/${version}-stream/BaseOS/x86_64/os/|" "$repo_file"
    sed -i "s|^metalink=.*centos-appstream-\$stream.*|baseurl=https://mirror.stream.centos.org/${version}-stream/AppStream/x86_64/os/|" "$repo_file"
    touch "$marker"
    break
  done
}

ensure_base_tools() {
  case "$OS_ID" in
    rocky|almalinux|centos|rhel|fedora)
      fix_centos_stream_repo_once
      if command -v dnf >/dev/null 2>&1; then
        dnf clean all >/dev/null 2>&1 || true
        dnf makecache --refresh >/dev/null 2>&1 || true
        dnf install -y python3 python3-pip python3-venv firewalld iptables iptables-services qrencode wireguard-tools || true
        dnf install -y python3.12 python3.12-pip python3.12-devel || true
      else
        yum install -y python3 python3-pip python3-venv firewalld iptables iptables-services qrencode wireguard-tools || true
      fi
      ;;
    ubuntu|debian|raspbian)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y python3 python3-pip python3-venv firewalld iptables qrencode wireguard
      ;;
    *)
      log "${RED}Unsupported OS: ${OS_ID}. LoomWG is designed for Rocky Linux and similar Linux distributions.${NC}"
      exit 1
      ;;
  esac
}

select_python() {
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version_output="$($candidate --version 2>/dev/null || true)"
      version_major="${version_output#Python }"
      version_major="${version_major%%.*}"
      if [[ "$candidate" == "python3" && "$version_major" != "3" ]]; then
        continue
      fi
      if [[ "$candidate" == "python3" ]]; then
        if ! python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
        then
          log "${RED}Found python3, but it is Python 3.$(python3 - <<'PY'
import sys
print(sys.version_info.minor)
PY
) and LoomWG requires Python 3.12+.${NC}"
          exit 1
        fi
      fi
      PYTHON_BIN="$candidate"
      return 0
    fi
  done

  log "${RED}Python 3.12+ is required but was not found after installation attempts.${NC}"
  exit 1
}

ensure_firewall() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now firewalld 2>/dev/null || true
  fi
}

ensure_ip_forwarding() {
  cat > /etc/sysctl.d/99-loomwg-forwarding.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF

  if command -v sysctl >/dev/null 2>&1; then
    sysctl --system >/dev/null 2>&1 || true
  fi
}

venv_ready() {
  local venv_dir="$SCRIPT_DIR/.venv"
  local python_bin="$venv_dir/bin/python"

  [[ -x "$python_bin" ]] || return 1

  "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

project_installed() {
  local venv_dir="$SCRIPT_DIR/.venv"
  local python_bin="$venv_dir/bin/python"

  [[ -x "$python_bin" ]] || return 1

  "$python_bin" - <<'PY'
import importlib.metadata
required = ["loomwg", "rich", "qrcode"]
for name in required:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit(1)
raise SystemExit(0)
PY
}

system_dependencies_ready() {
  command -v wg >/dev/null 2>&1 && command -v qrencode >/dev/null 2>&1
}

prepare_python_env() {
  select_python
  VENV_DIR="$SCRIPT_DIR/.venv"

  if [[ ! -d "$VENV_DIR" ]]; then
    log "${GREEN}Creating virtual environment in $VENV_DIR with ${PYTHON_BIN}${NC}"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  elif ! venv_ready; then
    log "${YELLOW}Existing virtual environment is missing Python 3.12+; recreating it.${NC}"
    rm -rf "$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  if ! project_installed; then
    log "${GREEN}Installing LoomWG in editable mode${NC}"
    python -m pip install --upgrade pip >/dev/null
    python -m pip install -e .
  else
    log "${GREEN}Using existing LoomWG environment${NC}"
  fi
}

start_app() {
  log "${GREEN}Starting LoomWG...${NC}"
  exec "$SCRIPT_DIR/.venv/bin/python" -m loom
}

main() {
  require_root "$@"
  detect_os

  if ! venv_ready || ! project_installed || ! system_dependencies_ready; then
    ensure_base_tools
    ensure_firewall
    ensure_ip_forwarding
    prepare_python_env
  else
    log "${GREEN}Python 3.12 venv and LoomWG dependencies are already present. Skipping package installation checks.${NC}"
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.venv/bin/activate"
  fi

  start_app
}

main "$@"
