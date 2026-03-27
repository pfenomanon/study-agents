#!/usr/bin/env bash
set -euo pipefail

# ZimaBoard 16GB installer/operator for study-agents backend.
# Uses Docker Compose with an additional Zima-focused override file.

ACTION="${1:-help}"
SERVICE="${2:-cag-service}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

BASE_COMPOSE_FILE="${BASE_COMPOSE_FILE:-docker-compose.yml}"
ZIMA_COMPOSE_FILE="${ZIMA_COMPOSE_FILE:-docker-compose.zimaboard.yml}"
SWAPFILE_PATH="${SWAPFILE_PATH:-/swapfile-study-agents}"
SWAP_SIZE_GB="${SWAP_SIZE_GB:-8}"
MIN_FREE_DISK_GB="${MIN_FREE_DISK_GB:-25}"
SYSCTL_FILE="${SYSCTL_FILE:-/etc/sysctl.d/99-study-agents-zimaboard.conf}"

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "This script needs root or sudo for host tuning tasks."
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

run_root() {
  if [[ -n "${SUDO}" ]]; then
    ${SUDO} "$@"
  else
    "$@"
  fi
}

is_debian_like() {
  [[ -f /etc/debian_version ]]
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

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' .env 2>/dev/null || true
}

vault_approle_ready() {
  local auth_method role_id_file secret_id_file
  auth_method="$(env_value VAULT_AUTH_METHOD)"
  [[ -n "${auth_method}" ]] || auth_method="token"
  if [[ "${auth_method}" != "approle" ]]; then
    return 1
  fi

  role_id_file="$(env_value VAULT_ROLE_ID_FILE)"
  secret_id_file="$(env_value VAULT_SECRET_ID_FILE)"
  [[ -n "${role_id_file}" ]] || role_id_file="/vault/bootstrap/role_id"
  [[ -n "${secret_id_file}" ]] || secret_id_file="/vault/bootstrap/secret_id"

  if [[ "${role_id_file}" == "/vault/bootstrap/role_id" ]]; then
    role_id_file="${ROOT_DIR}/docker/vault/runtime/role_id"
  fi
  if [[ "${secret_id_file}" == "/vault/bootstrap/secret_id" ]]; then
    secret_id_file="${ROOT_DIR}/docker/vault/runtime/secret_id"
  fi

  [[ -s "${role_id_file}" && -s "${secret_id_file}" ]]
}

compose() {
  docker compose -f "${BASE_COMPOSE_FILE}" -f "${ZIMA_COMPOSE_FILE}" "$@"
}

require_files() {
  [[ -f "${BASE_COMPOSE_FILE}" ]] || die "Missing ${BASE_COMPOSE_FILE}"
  [[ -f "${ZIMA_COMPOSE_FILE}" ]] || die "Missing ${ZIMA_COMPOSE_FILE}"
}

detect_mem_mb() {
  awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo
}

detect_free_disk_gb() {
  df -BG "${ROOT_DIR}" | awk 'NR==2 {gsub("G","",$4); print int($4)}'
}

check_platform() {
  local arch mem_mb free_disk_gb
  arch="$(uname -m)"
  mem_mb="$(detect_mem_mb)"
  free_disk_gb="$(detect_free_disk_gb)"

  [[ "${arch}" == "x86_64" ]] || die "Unsupported architecture: ${arch}. Expected x86_64."
  if (( mem_mb < 14000 )); then
    die "Detected ${mem_mb}MB RAM. This workflow targets a 16GB-class host."
  fi
  if (( free_disk_gb < MIN_FREE_DISK_GB )); then
    die "Only ${free_disk_gb}GB free disk. Need at least ${MIN_FREE_DISK_GB}GB."
  fi
}

ensure_env_file() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    log "Created .env from .env.example"
    log "Edit .env with OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY before start, or bootstrap Vault AppRole first."
  fi
}

validate_required_env() {
  local missing=0
  local key value
  if vault_approle_ready; then
    ensure_required_tokens
    return 0
  fi
  for key in OPENAI_API_KEY SUPABASE_URL SUPABASE_KEY; do
    value="$(env_value "${key}")"
    if is_placeholder_value "${value}"; then
      echo "Missing or placeholder value in .env: ${key}" >&2
      missing=1
    fi
  done
  ensure_required_tokens
  (( missing == 0 )) || die "Populate required .env values before start."
}

install_deps() {
  if ! is_debian_like; then
    die "Automatic dependency installation currently supports Debian/Ubuntu."
  fi
  log "Installing host dependencies..."
  run_root apt-get update -y
  run_root apt-get install -y docker.io docker-compose-plugin curl jq ca-certificates python3
  run_root systemctl enable --now docker
}

token_required() {
  local key="$1"
  local default_true="${2:-true}"
  local raw
  raw="$(env_value "${key}")"
  if [[ -z "${raw}" ]]; then
    is_true "${default_true}"
    return
  fi
  is_true "${raw}"
}

ensure_required_tokens() {
  if vault_approle_ready; then
    return 0
  fi

  local need_generate=0
  local api_token rag_token copilot_key
  local api_required=0 rag_required=0 copilot_required=0

  api_token="$(env_value API_TOKEN)"
  rag_token="$(env_value RAG_API_TOKEN)"
  copilot_key="$(env_value COPILOT_API_KEY)"

  if token_required API_REQUIRE_TOKEN true; then
    api_required=1
    if [[ -z "${api_token}" ]]; then
      need_generate=1
    fi
  fi
  if token_required RAG_REQUIRE_TOKEN true; then
    rag_required=1
    if [[ -z "${rag_token}" && -z "${api_token}" ]]; then
      need_generate=1
    fi
  fi
  if token_required COPILOT_REQUIRE_TOKEN true; then
    copilot_required=1
    if [[ -z "${copilot_key}" && -z "${api_token}" ]]; then
      need_generate=1
    fi
  fi

  if (( need_generate == 1 )); then
    log "Generating missing service tokens in .env..."
    bash "${SCRIPT_DIR}/generate_local_api_keys.sh" --write-env >/dev/null
    api_token="$(env_value API_TOKEN)"
    rag_token="$(env_value RAG_API_TOKEN)"
    copilot_key="$(env_value COPILOT_API_KEY)"
  fi

  if (( api_required == 1 )) && [[ -z "${api_token}" ]]; then
    die "API_REQUIRE_TOKEN=true but API_TOKEN is empty. Set API_TOKEN or set API_REQUIRE_TOKEN=false."
  fi
  if (( rag_required == 1 )) && [[ -z "${rag_token}" && -z "${api_token}" ]]; then
    die "RAG_REQUIRE_TOKEN=true but both RAG_API_TOKEN and API_TOKEN are empty."
  fi
  if (( copilot_required == 1 )) && [[ -z "${copilot_key}" && -z "${api_token}" ]]; then
    die "COPILOT_REQUIRE_TOKEN=true but both COPILOT_API_KEY and API_TOKEN are empty."
  fi
}

ensure_docker_group_access() {
  if [[ "${EUID}" -eq 0 ]]; then
    return 0
  fi
  if id -nG "${USER}" | tr ' ' '\n' | grep -qx docker; then
    return 0
  fi
  run_root usermod -aG docker "${USER}"
  log "Added ${USER} to docker group."
  log "Open a new shell session before running non-root Docker commands."
}

configure_swap() {
  local target_mb
  target_mb=$((SWAP_SIZE_GB * 1024))

  if swapon --show=NAME --noheadings | grep -qx "${SWAPFILE_PATH}"; then
    log "Swap already active at ${SWAPFILE_PATH}"
  else
    if [[ ! -f "${SWAPFILE_PATH}" ]]; then
      log "Creating ${SWAP_SIZE_GB}GB swapfile at ${SWAPFILE_PATH}"
      if ! run_root fallocate -l "${SWAP_SIZE_GB}G" "${SWAPFILE_PATH}" 2>/dev/null; then
        run_root dd if=/dev/zero of="${SWAPFILE_PATH}" bs=1M count="${target_mb}" status=progress
      fi
      run_root chmod 600 "${SWAPFILE_PATH}"
      run_root mkswap "${SWAPFILE_PATH}"
    fi
    run_root swapon "${SWAPFILE_PATH}"
  fi

  if ! grep -qF "${SWAPFILE_PATH} none swap sw 0 0" /etc/fstab; then
    echo "${SWAPFILE_PATH} none swap sw 0 0" | run_root tee -a /etc/fstab >/dev/null
  fi
}

configure_sysctl() {
  log "Applying kernel tuning for memory pressure and file watchers..."
  cat <<EOF | run_root tee "${SYSCTL_FILE}" >/dev/null
vm.swappiness=10
vm.vfs_cache_pressure=50
fs.inotify.max_user_instances=1024
fs.inotify.max_user_watches=524288
EOF
  run_root sysctl --system >/dev/null
}

show_preflight_summary() {
  local mem_mb free_disk_gb
  mem_mb="$(detect_mem_mb)"
  free_disk_gb="$(detect_free_disk_gb)"
  log "Platform checks passed."
  echo "Architecture : $(uname -m)"
  echo "Memory (MB)  : ${mem_mb}"
  echo "Free disk GB : ${free_disk_gb}"
  echo "Swap active  :"
  swapon --show || true
}

prepare_host() {
  require_files
  check_platform
  install_deps
  ensure_docker_group_access
  ensure_env_file
  configure_swap
  configure_sysctl
  show_preflight_summary
}

start_stack() {
  prepare_host
  validate_required_env
  compose config -q
  log "Starting ZimaBoard profile stack..."
  compose up -d --build
  bash "${SCRIPT_DIR}/validate_zimaboard_stack.sh"
}

restart_stack() {
  require_files
  validate_required_env
  compose config -q
  log "Restarting ZimaBoard profile stack..."
  compose down
  compose up -d --build
  bash "${SCRIPT_DIR}/validate_zimaboard_stack.sh"
}

start_optional_tools() {
  require_files
  compose config -q
  log "Starting optional utility-service (profile=tools)..."
  COMPOSE_PROFILES=tools compose up -d utility-service
  compose ps
}

start_optional_vault() {
  require_files
  compose config -q
  log "Starting optional vault service (profile=vault)..."
  COMPOSE_PROFILES=vault compose up -d vault
  compose ps
}

print_help() {
  cat <<'EOF'
Usage: bash scripts/install_zimaboard_16gb.sh <action> [service]

Actions:
  prepare       Install host deps, configure swap/sysctl, and scaffold .env
  start         prepare + config validation + docker compose up -d --build + smoke checks
  restart       Recreate stack with current images/build context + smoke checks
  start-tools   Start optional utility-service profile
  start-vault   Start optional vault profile
  validate      Run runtime validation checks
  status        Show docker compose status using Zima override
  logs [name]   Tail logs (default: cag-service)
  stop          Stop stack (compose down)
  help          Show this message

Environment overrides:
  SWAP_SIZE_GB        Swap size in GB (default: 8)
  SWAPFILE_PATH       Swap path (default: /swapfile-study-agents)
  MIN_FREE_DISK_GB    Required free disk before install (default: 25)
  BASE_COMPOSE_FILE   Base compose file (default: docker-compose.yml)
  ZIMA_COMPOSE_FILE   Zima compose override (default: docker-compose.zimaboard.yml)
EOF
}

case "${ACTION}" in
  prepare)
    prepare_host
    ;;
  start)
    start_stack
    ;;
  restart)
    restart_stack
    ;;
  start-tools)
    start_optional_tools
    ;;
  start-vault)
    start_optional_vault
    ;;
  validate)
    bash "${SCRIPT_DIR}/validate_zimaboard_stack.sh"
    ;;
  status)
    require_files
    compose ps
    ;;
  logs)
    require_files
    compose logs -f "${SERVICE}"
    ;;
  stop)
    require_files
    compose down
    ;;
  help|-h|--help)
    print_help
    ;;
  *)
    die "Unknown action '${ACTION}'. Use: bash scripts/install_zimaboard_16gb.sh help"
    ;;
esac
