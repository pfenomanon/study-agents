#!/usr/bin/env bash
set -euo pipefail

# Install and operate the study-agents backend stack on Debian/Ubuntu VPS hosts.
# Usage:
#   bash scripts/install_backend_vps.sh deps
#   bash scripts/install_backend_vps.sh start
#   bash scripts/install_backend_vps.sh deploy
#   bash scripts/install_backend_vps.sh start-local-all
#   bash scripts/install_backend_vps.sh bootstrap-vault-nondev
#   bash scripts/install_backend_vps.sh configure-lan-https <public-domain-or-ip> [allow-cidr]
#   bash scripts/install_backend_vps.sh export-caddy-ca [output-path]
#   bash scripts/install_backend_vps.sh reclaim-disk
#   bash scripts/install_backend_vps.sh validate
#   bash scripts/install_backend_vps.sh status
#   bash scripts/install_backend_vps.sh logs
#   bash scripts/install_backend_vps.sh stop

ACTION="${1:-start}"
ARG1="${2:-}"
ARG2="${3:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
  cat <<'EOF'
Usage: bash scripts/install_backend_vps.sh [deps|start|deploy|start-local-all|bootstrap-vault-nondev|configure-lan-https|export-caddy-ca|reclaim-disk|apply-schema|restart|validate|status|logs|stop]

Actions:
  deps             Install host dependencies and create .env if missing
  start            Validate env + run docker compose up -d --build
  deploy           deps + start + backend validation checks (recommended)
  start-local-all  Install deps + start local Supabase + apply schema + start backend stack
  bootstrap-vault-nondev
                   Configure persistent Vault + AppRole + OIDC admin flow (non-dev mode)
  configure-lan-https <public-domain-or-ip> [allow-cidr]
                   Configure HTTPS gateway for LAN usage, bootstrap Authelia, recreate gateway/auth
  export-caddy-ca [output-path]
                   Export Caddy local root CA certificate for client trust import
  reclaim-disk     Reclaim host disk space (docker build cache/images + apt/journal cleanup)
  apply-schema     Apply supabase_schema.sql using SUPABASE_DB_URL (or detected local DB URL)
  restart          Restart stack
  validate         Validate running stack services and endpoints
  status           Show docker compose ps
  logs             Tail cag-service logs
  stop             Stop stack
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

die() {
  echo "ERROR: $*" >&2
  exit 1
}

is_debian_like() {
  [[ -f /etc/debian_version ]]
}

pkg_installed() {
  dpkg -s "$1" >/dev/null 2>&1
}

ensure_pkg() {
  local pkg="$1"
  if pkg_installed "$pkg"; then
    return 0
  fi
  log "Installing package: ${pkg}"
  $SUDO apt-get install -y "$pkg"
}

get_env() {
  local key="$1"
  [[ -f .env ]] || return 0
  awk -F= -v key="$key" '$1 == key {print substr($0, index($0, $2)); exit}' .env
}

set_env() {
  local key="$1"
  local value="$2"
  local tmp
  if [[ ! -f .env ]]; then
    touch .env
  fi
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") { print key "=" value; updated = 1; next }
    { print }
    END {
      if (!updated) {
        print key "=" value
      }
    }
  ' .env > "$tmp"
  mv "$tmp" .env
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

is_true() {
  local raw
  raw="$(lower "${1:-}")"
  case "$raw" in
    1|true|yes|on)
      return 0
      ;;
    0|false|no|off)
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

is_placeholder_value() {
  local value="$1"
  [[ -z "$value" ]] && return 0
  [[ "$value" == your-* ]] && return 0
  [[ "$value" == "<"* ]] && return 0
  return 1
}

ensure_env_file() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    log "Created .env from .env.example"
  fi
}

install_deps() {
  if ! is_debian_like; then
    die "Automatic dependency installation currently supports Debian/Ubuntu."
  fi

  log "Updating apt cache..."
  $SUDO apt-get update -y

  if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Engine + Compose package..."
    if ! $SUDO apt-get install -y docker.io docker-compose-plugin; then
      $SUDO apt-get install -y docker.io docker-compose-v2
    fi
    $SUDO systemctl enable --now docker || true
  else
    log "Docker already installed."
  fi

  if ! docker compose version >/dev/null 2>&1; then
    log "Installing docker compose package..."
    if ! $SUDO apt-get install -y docker-compose-plugin; then
      $SUDO apt-get install -y docker-compose-v2
    fi
  fi
  ensure_pkg curl
  ensure_pkg ca-certificates
  ensure_pkg git
  ensure_pkg openssl
  ensure_pkg postgresql-client
  ensure_pkg jq
  ensure_pkg python3
  ensure_pkg python3-yaml
  ensure_pkg python3-venv
  ensure_pkg unzip

  if command -v systemctl >/dev/null 2>&1; then
    $SUDO systemctl enable --now docker || true
  fi

  if ! (docker info >/dev/null 2>&1 || $SUDO docker info >/dev/null 2>&1); then
    die "Docker Engine is installed but not reachable. Check docker service status."
  fi

  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose is not available after dependency install."
  fi

  ensure_env_file
}

install_supabase_cli() {
  if command -v supabase >/dev/null 2>&1; then
    return 0
  fi

  log "Installing Supabase CLI..."
  if curl -fsSL https://app.supabase.com/api/install/cli | sh; then
    export PATH="$HOME/.supabase/bin:$PATH"
  else
    local arch asset tmp_dir
    arch="$(uname -m)"
    case "$arch" in
      x86_64)
        asset="supabase_linux_amd64.tar.gz"
        ;;
      aarch64|arm64)
        asset="supabase_linux_arm64.tar.gz"
        ;;
      *)
        die "Unsupported architecture for Supabase CLI fallback installer: ${arch}"
        ;;
    esac
    tmp_dir="$(mktemp -d)"
    curl -fL "https://github.com/supabase/cli/releases/latest/download/${asset}" \
      -o "${tmp_dir}/supabase.tar.gz"
    tar -xzf "${tmp_dir}/supabase.tar.gz" -C "${tmp_dir}"
    $SUDO install -m 755 "${tmp_dir}/supabase" /usr/local/bin/supabase
    rm -rf "${tmp_dir}"
  fi

  export PATH="$HOME/.supabase/bin:$PATH"
  command -v supabase >/dev/null 2>&1 || die "Supabase CLI installation failed."
}

validate_core_env() {
  local key value missing=0
  ensure_env_file
  for key in OPENAI_API_KEY SUPABASE_URL SUPABASE_KEY; do
    value="$(get_env "$key")"
    if is_placeholder_value "$value"; then
      echo "Missing or placeholder value in .env: ${key}" >&2
      missing=1
    fi
  done
  (( missing == 0 )) || die "Populate required .env values before starting services."
}

token_required() {
  local key="$1"
  local default_true="${2:-true}"
  local raw
  raw="$(get_env "$key")"
  if [[ -z "$raw" ]]; then
    is_true "$default_true"
    return
  fi
  is_true "$raw"
}

ensure_required_tokens() {
  local need_generate=0
  local api_token rag_token copilot_key
  local api_required=0 rag_required=0 copilot_required=0
  api_token="$(get_env API_TOKEN)"
  rag_token="$(get_env RAG_API_TOKEN)"
  copilot_key="$(get_env COPILOT_API_KEY)"

  if token_required API_REQUIRE_TOKEN true; then
    api_required=1
    if [[ -z "$api_token" ]]; then
      need_generate=1
    fi
  fi
  if token_required RAG_REQUIRE_TOKEN true; then
    rag_required=1
    if [[ -z "$rag_token" && -z "$api_token" ]]; then
      need_generate=1
    fi
  fi
  if token_required COPILOT_REQUIRE_TOKEN true; then
    copilot_required=1
    if [[ -z "$copilot_key" && -z "$api_token" ]]; then
      need_generate=1
    fi
  fi

  if (( need_generate == 1 )); then
    log "Generating missing service tokens in .env..."
    bash scripts/generate_local_api_keys.sh --write-env >/dev/null
    api_token="$(get_env API_TOKEN)"
    rag_token="$(get_env RAG_API_TOKEN)"
    copilot_key="$(get_env COPILOT_API_KEY)"
  fi

  if (( api_required == 1 )) && [[ -z "$api_token" ]]; then
    die "API_REQUIRE_TOKEN=true but API_TOKEN is empty. Set API_TOKEN or set API_REQUIRE_TOKEN=false."
  fi
  if (( rag_required == 1 )) && [[ -z "$rag_token" && -z "$api_token" ]]; then
    die "RAG_REQUIRE_TOKEN=true but both RAG_API_TOKEN and API_TOKEN are empty."
  fi
  if (( copilot_required == 1 )) && [[ -z "$copilot_key" && -z "$api_token" ]]; then
    die "COPILOT_REQUIRE_TOKEN=true but both COPILOT_API_KEY and API_TOKEN are empty."
  fi
}

resolve_supabase_db_url() {
  local candidate status_env status_json status_plain
  candidate="${SUPABASE_DB_URL:-$(get_env SUPABASE_DB_URL)}"
  if [[ -n "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  if ! command -v supabase >/dev/null 2>&1; then
    return 1
  fi

  status_env="$(supabase status -o env 2>/dev/null || supabase status --env 2>/dev/null || true)"
  candidate="$(
    printf '%s\n' "$status_env" \
      | awk -F= '/^(SUPABASE_DB_URL|DB_URL|POSTGRES_URL|POSTGRES_CONNECTION_STRING)=/ {print substr($0, index($0, $2)); exit}'
  )"
  if [[ -n "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  status_json="$(supabase status -o json 2>/dev/null || supabase status --json 2>/dev/null || true)"
  candidate="$(
    STATUS_JSON="$status_json" python3 - <<'PY' 2>/dev/null || true
import json
import os

raw = os.environ.get("STATUS_JSON", "").strip()
if not raw:
    raise SystemExit(0)
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(0)

def first_nonempty(values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

candidates = []
if isinstance(data, dict):
    candidates.extend(
        [
            data.get("db_url"),
            data.get("database_url"),
            data.get("DB URL"),
            data.get("Postgres URL"),
            data.get("postgres_url"),
        ]
    )
    services = data.get("services")
    if isinstance(services, dict):
        db_service = services.get("db")
        if isinstance(db_service, dict):
            candidates.extend(
                [
                    db_service.get("url"),
                    db_service.get("db_url"),
                    db_service.get("postgres_url"),
                ]
            )

value = first_nonempty(candidates)
if value:
    print(value)
PY
  )"
  if [[ -n "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  status_plain="$(supabase status 2>/dev/null || true)"
  candidate="$(
    printf '%s\n' "$status_plain" \
      | sed -n 's/^[[:space:]]*DB URL:[[:space:]]*//p' \
      | head -n1
  )"
  if [[ -n "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi

  return 1
}

apply_supabase_schema() {
  local db_url
  if [[ ! -f supabase_schema.sql ]]; then
    die "supabase_schema.sql not found in ${ROOT_DIR}"
  fi
  db_url="$(resolve_supabase_db_url || true)"
  if [[ -z "$db_url" ]]; then
    die "Could not determine a Postgres connection URL. Set SUPABASE_DB_URL in .env or export it before running apply-schema."
  fi
  set_env SUPABASE_DB_URL "$db_url"
  log "Applying supabase_schema.sql..."
  psql "$db_url" -v ON_ERROR_STOP=1 -f supabase_schema.sql
}

validate_runtime_env() {
  validate_core_env
  ensure_required_tokens
}

run_stack_validation() {
  local validator="${SCRIPT_DIR}/validate_backend_stack.sh"
  if [[ ! -f "$validator" ]]; then
    die "Missing validator script: ${validator}"
  fi
  chmod +x "$validator"

  log "Running backend stack validation..."
  if docker compose ps >/dev/null 2>&1; then
    bash "$validator"
  else
    $SUDO bash "$validator"
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

docker_group_has_user() {
  local account
  account="${SUDO_USER:-$USER}"
  getent group docker 2>/dev/null | awk -F: -v account="$account" '
    BEGIN { found = 0 }
    {
      split($4, members, ",")
      for (i in members) {
        if (members[i] == account) {
          found = 1
        }
      }
    }
    END { exit(found ? 0 : 1) }
  '
}

run_repo_script_with_docker_access() {
  local script_path="$1"
  shift || true
  local cmd=(bash "$script_path" "$@")

  if docker info >/dev/null 2>&1; then
    (
      cd "$ROOT_DIR"
      PATH="$HOME/.local/bin:$PATH" "${cmd[@]}"
    )
    return 0
  fi

  if command -v sg >/dev/null 2>&1 && docker_group_has_user; then
    local cmd_joined="" part root_q path_q
    for part in "${cmd[@]}"; do
      printf -v part "%q" "$part"
      cmd_joined+="${part} "
    done
    printf -v root_q "%q" "$ROOT_DIR"
    printf -v path_q "%q" "$HOME/.local/bin:$PATH"
    sg docker -c "cd ${root_q} && PATH=${path_q} ${cmd_joined}"
    return 0
  fi

  die "Docker access unavailable for this shell. Re-login (or run 'newgrp docker') and retry."
}

case "$ACTION" in
  deps)
    install_deps
    ;;
  start)
    install_deps
    validate_runtime_env
    log "Starting backend stack..."
    dc up -d --build
    dc ps
    ;;
  deploy)
    install_deps
    validate_runtime_env
    log "Deploying backend stack..."
    dc up -d --build
    dc ps
    run_stack_validation
    ;;
  start-local-all)
    install_deps
    install_supabase_cli
    log "Starting local Supabase..."
    run_repo_script_with_docker_access scripts/setup_local_supabase.sh
    validate_runtime_env
    apply_supabase_schema
    log "Starting backend stack..."
    dc up -d --build
    dc ps
    run_stack_validation
    ;;
  bootstrap-vault-nondev)
    install_deps
    run_repo_script_with_docker_access scripts/bootstrap_vault_nondev.sh
    ;;
  configure-lan-https)
    install_deps
    run_repo_script_with_docker_access scripts/configure_lan_https.sh "$ARG1" "$ARG2"
    ;;
  export-caddy-ca)
    install_deps
    run_repo_script_with_docker_access scripts/export_caddy_root_ca.sh "$ARG1"
    ;;
  reclaim-disk)
    install_deps
    run_repo_script_with_docker_access scripts/reclaim_disk_space.sh
    ;;
  apply-schema)
    install_deps
    if [[ -z "${SUPABASE_DB_URL:-$(get_env SUPABASE_DB_URL)}" ]]; then
      install_supabase_cli
    fi
    apply_supabase_schema
    ;;
  restart)
    install_deps
    validate_runtime_env
    log "Restarting backend stack..."
    dc down
    dc up -d --build
    dc ps
    run_stack_validation
    ;;
  validate)
    ensure_env_file
    validate_runtime_env
    run_stack_validation
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
