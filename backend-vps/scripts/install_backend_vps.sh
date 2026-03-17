#!/usr/bin/env bash
set -euo pipefail

# Install and operate the study-agents backend stack on Debian/Ubuntu VPS hosts.
# Usage:
#   bash scripts/install_backend_vps.sh deps
#   bash scripts/install_backend_vps.sh start
#   bash scripts/install_backend_vps.sh start-local-all
#   bash scripts/install_backend_vps.sh status
#   bash scripts/install_backend_vps.sh logs
#   bash scripts/install_backend_vps.sh stop

ACTION="${1:-start}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  cat <<'EOF'
Usage: bash scripts/install_backend_vps.sh [deps|start|restart|start-local-all|restart-local-all|status|status-all|logs|stop|stop-local-all]

Actions:
  deps              Install Docker + Compose plugin and create .env if missing
  start             Ensure deps + run app docker compose up -d --build
  restart           Restart app docker compose stack
  start-local-all   Start local Supabase (Docker), apply schema, then start app stack
  restart-local-all Restart local Supabase + app stack
  status            Show app docker compose status
  status-all        Show app status + local Supabase status
  logs              Tail cag-service logs
  stop              Stop app docker compose stack
  stop-local-all    Stop app stack + local Supabase stack
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

docker_ok() {
  docker info >/dev/null 2>&1
}

docker_cmd() {
  if docker_ok; then
    docker "$@"
  else
    $SUDO docker "$@"
  fi
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
    docker_cmd compose "$@"
  else
    echo "docker compose is not available."
    exit 1
  fi
}

supabase_cmd() {
  export PATH="$HOME/.supabase/bin:$PATH"
  if command -v supabase >/dev/null 2>&1; then
    supabase "$@"
    return
  fi
  echo "Supabase CLI not found. Run ./scripts/setup_local_supabase.sh once to install it."
  exit 1
}

start_local_supabase() {
  log "Starting local Supabase stack..."
  chmod +x "${SCRIPT_DIR}/setup_local_supabase.sh"
  "${SCRIPT_DIR}/setup_local_supabase.sh"
}

stop_local_supabase() {
  local project_id expected_db
  project_id="$(basename "$ROOT_DIR")"
  expected_db="supabase_db_${project_id}"
  if command -v supabase >/dev/null 2>&1 || [[ -x "$HOME/.supabase/bin/supabase" ]]; then
    if docker_cmd ps -a --format '{{.Names}}' | grep -qx "$expected_db"; then
      log "Stopping local Supabase stack..."
      supabase_cmd stop || true
    else
      log "Local Supabase stack is not present for project '${project_id}'; skipping stop."
    fi
  else
    log "Supabase CLI not installed; skipping local Supabase stop."
  fi
}

status_local_supabase() {
  local project_id expected_db
  project_id="$(basename "$ROOT_DIR")"
  expected_db="supabase_db_${project_id}"
  if command -v supabase >/dev/null 2>&1 || [[ -x "$HOME/.supabase/bin/supabase" ]]; then
    if docker_cmd ps -a --format '{{.Names}}' | grep -qx "$expected_db"; then
      log "Local Supabase status:"
      supabase_cmd status || true
    else
      log "Local Supabase is not running for project '${project_id}'."
      log "Run: bash scripts/install_backend_vps.sh start-local-all"
    fi
  else
    log "Supabase CLI not installed; no local Supabase status available."
  fi
}

apply_local_supabase_schema() {
  local project_id db_container
  project_id="$(basename "$ROOT_DIR")"
  db_container="supabase_db_${project_id}"
  if ! docker_cmd ps --format '{{.Names}}' | grep -qx "$db_container"; then
    # Fallback for unusual Supabase project naming; still stay within this host.
    db_container="$(docker_cmd ps --format '{{.Names}}' | grep '^supabase_db_' | head -n1 || true)"
  fi
  if [[ -z "$db_container" ]]; then
    echo "Could not find local Supabase DB container (expected name prefix: supabase_db_)."
    exit 1
  fi

  log "Applying supabase_schema.sql to local Supabase DB (${db_container})..."
  docker_cmd exec -i "$db_container" psql -U postgres -d postgres < "${ROOT_DIR}/supabase_schema.sql"
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
  start-local-all)
    install_deps
    start_local_supabase
    apply_local_supabase_schema
    log "Starting backend app stack..."
    dc up -d --build
    dc ps
    status_local_supabase
    ;;
  restart-local-all)
    install_deps
    dc down || true
    stop_local_supabase
    start_local_supabase
    apply_local_supabase_schema
    log "Restarting backend app stack..."
    dc up -d --build
    dc ps
    status_local_supabase
    ;;
  status)
    dc ps
    ;;
  status-all)
    dc ps
    status_local_supabase
    ;;
  logs)
    dc logs -f cag-service
    ;;
  stop)
    dc down
    ;;
  stop-local-all)
    dc down || true
    stop_local_supabase
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Run: bash scripts/install_backend_vps.sh --help"
    exit 1
    ;;
esac
