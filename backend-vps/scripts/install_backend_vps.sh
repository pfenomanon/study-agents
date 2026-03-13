#!/usr/bin/env bash
set -euo pipefail

# Install and operate the study-agents backend stack on Debian/Ubuntu VPS hosts.
# Usage:
#   bash scripts/install_backend_vps.sh deps
#   bash scripts/install_backend_vps.sh start
#   bash scripts/install_backend_vps.sh status
#   bash scripts/install_backend_vps.sh logs
#   bash scripts/install_backend_vps.sh stop

ACTION="${1:-start}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  cat <<'EOF'
Usage: bash scripts/install_backend_vps.sh [deps|start|restart|status|logs|stop]

Actions:
  deps     Install Docker + Compose plugin and create .env if missing
  start    Ensure deps + run docker compose up -d --build
  restart  Restart stack
  status   Show docker compose ps
  logs     Tail cag-service logs
  stop     Stop stack
EOF
  exit 0
fi

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "This script needs root or sudo to install dependencies."
    exit 1
  fi
fi

log() {
  echo "==> $*"
}

is_debian_like() {
  [[ -f /etc/debian_version ]]
}

install_deps() {
  if ! command -v docker >/dev/null 2>&1; then
    if ! is_debian_like; then
      echo "Unsupported OS for automatic install. Install Docker manually, then rerun."
      exit 1
    fi
    log "Installing Docker Engine + Compose plugin..."
    $SUDO apt-get update -y
    $SUDO apt-get install -y docker.io docker-compose-plugin
    $SUDO systemctl enable --now docker || true
  else
    log "Docker already installed."
  fi

  if ! docker compose version >/dev/null 2>&1; then
    if ! is_debian_like; then
      echo "docker compose plugin missing. Install manually, then rerun."
      exit 1
    fi
    log "Installing docker-compose-plugin..."
    $SUDO apt-get update -y
    $SUDO apt-get install -y docker-compose-plugin
  fi

  if [[ ! -f .env ]]; then
    cp .env.example .env
    log "Created .env from .env.example"
    log "Edit .env now before first start (OPENAI/SUPABASE/API settings)."
  fi
}

dc() {
  if docker compose version >/dev/null 2>&1; then
    if docker compose ps >/dev/null 2>&1; then
      docker compose "$@"
    else
      $SUDO docker compose "$@"
    fi
  else
    echo "docker compose is not available."
    exit 1
  fi
}

case "$ACTION" in
  deps)
    install_deps
    ;;
  start)
    install_deps
    log "Starting backend stack..."
    dc up -d --build
    dc ps
    ;;
  restart)
    install_deps
    log "Restarting backend stack..."
    dc down
    dc up -d --build
    dc ps
    ;;
  status)
    dc ps
    ;;
  logs)
    dc logs -f cag-service
    ;;
  stop)
    dc down
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Run: bash scripts/install_backend_vps.sh --help"
    exit 1
    ;;
esac

