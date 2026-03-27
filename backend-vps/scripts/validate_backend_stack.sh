#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

WAIT_RETRIES="${WAIT_RETRIES:-45}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

dc() {
  docker compose "$@"
}

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, length(key) + 2); exit}' .env 2>/dev/null || true
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

runtime_env_value() {
  local key="$1"
  local service value
  for service in cag-service rag-service copilot-service; do
    if ! dc ps --status running --services | grep -qx "${service}"; then
      continue
    fi
    value="$(dc exec -T "${service}" /bin/sh -lc "grep -m1 '^${key}=' /env/.env.runtime 2>/dev/null | cut -d= -f2- || true" 2>/dev/null || true)"
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
  done
  return 1
}

resolve_validation_secret() {
  local key="$1"
  local value
  value="$(env_value "${key}")"
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi

  value="$(runtime_env_value "${key}" || true)"
  printf '%s' "${value}"
}

wait_for_required_services() {
  local attempt running service
  for ((attempt=1; attempt<=WAIT_RETRIES; attempt++)); do
    running="$(dc ps --status running --services | tr '\n' ' ')"
    local missing=0
    for service in cag-service rag-service copilot-service copilot-frontend redis authelia tls-gateway; do
      if [[ " ${running} " != *" ${service} "* ]]; then
        missing=1
        break
      fi
    done

    if (( missing == 0 )); then
      log "All required services are running."
      return 0
    fi

    sleep "${WAIT_INTERVAL_SECONDS}"
  done

  dc ps || true
  die "Required services failed to reach running state in time."
}

check_http_code() {
  local name="$1"
  local expected_csv="$2"
  shift 2

  local status_file="${TMPDIR}/${name}.status"
  local body_file="${TMPDIR}/${name}.body"
  local attempt code

  for ((attempt=1; attempt<=WAIT_RETRIES; attempt++)); do
    if ! curl -sS -m 25 -o "${body_file}" -w '%{http_code}' "$@" > "${status_file}"; then
      sleep "${WAIT_INTERVAL_SECONDS}"
      continue
    fi

    code="$(cat "${status_file}")"
    IFS=',' read -r -a expected_codes <<< "${expected_csv}"
    for expected in "${expected_codes[@]}"; do
      if [[ "${code}" == "${expected}" ]]; then
        log "${name}: HTTP ${code} (acceptable)"
        return 0
      fi
    done
    sleep "${WAIT_INTERVAL_SECONDS}"
  done

  echo "--- ${name} response body ---" >&2
  sed -n '1,80p' "${body_file}" >&2 || true
  die "${name}: unexpected HTTP status after retries (expected one of [${expected_csv}])"
}

check_vault_health() {
  local auth_method allow_unready expected_code_csv
  local vault_running=0
  local -a ca_args=()

  auth_method="$(env_value VAULT_AUTH_METHOD)"
  if [[ -z "${auth_method}" ]]; then
    auth_method="token"
  fi

  if dc ps --status running --services | grep -qx vault; then
    vault_running=1
  fi

  if (( vault_running == 0 )); then
    if [[ "${auth_method}" == "approle" ]]; then
      die "VAULT_AUTH_METHOD=approle but vault service is not running."
    fi
    return 0
  fi

  if [[ -f "${ROOT_DIR}/docker/internal-tls/vault-ca.pem" ]]; then
    ca_args=(--cacert "${ROOT_DIR}/docker/internal-tls/vault-ca.pem")
  else
    # Keep validation available even before TLS bootstrap is generated.
    ca_args=(-k)
  fi

  expected_code_csv="200,429,472,473"
  allow_unready="$(env_value VAULT_ALLOW_UNREADY)"
  if is_true "${allow_unready}"; then
    expected_code_csv="${expected_code_csv},501,503"
  fi

  check_http_code \
    "vault-health" \
    "${expected_code_csv}" \
    "${ca_args[@]}" \
    "https://127.0.0.1:8200/v1/sys/health"

  if [[ "${auth_method}" == "approle" ]]; then
    [[ -s "${ROOT_DIR}/docker/vault/runtime/role_id" ]] || die "Missing AppRole role_id file: docker/vault/runtime/role_id"
    [[ -s "${ROOT_DIR}/docker/vault/runtime/secret_id" ]] || die "Missing AppRole secret_id file: docker/vault/runtime/secret_id"
    log "Vault AppRole runtime files are present."
  fi
}

run_vault_workflow_validation() {
  local auth_method vault_running=0
  auth_method="$(env_value VAULT_AUTH_METHOD)"
  if [[ -z "${auth_method}" ]]; then
    auth_method="token"
  fi

  if dc ps --status running --services | grep -qx vault; then
    vault_running=1
  fi

  if [[ "${auth_method}" != "approle" || "${vault_running}" -eq 0 ]]; then
    return 0
  fi

  if [[ -x "${SCRIPT_DIR}/validate_vault_workflows.sh" ]]; then
    log "Running Vault AppRole workflow validation..."
    bash "${SCRIPT_DIR}/validate_vault_workflows.sh"
  else
    die "Missing Vault workflow validator: ${SCRIPT_DIR}/validate_vault_workflows.sh"
  fi
}

main() {
  [[ -f docker-compose.yml ]] || die "Missing docker-compose.yml"
  [[ -f .env ]] || die "Missing .env"
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin is not available"

  log "Validating compose syntax..."
  dc config -q

  log "Waiting for required services to become healthy..."
  wait_for_required_services

  check_vault_health
  run_vault_workflow_validation

  local api_token rag_token copilot_key auth_header_cag=() auth_header_rag=() auth_header_copilot=()
  if is_true "${VALIDATE_USE_AUTH_HEADERS:-false}"; then
    api_token="$(resolve_validation_secret API_TOKEN)"
    rag_token="$(resolve_validation_secret RAG_API_TOKEN)"
    copilot_key="$(resolve_validation_secret COPILOT_API_KEY)"

    if [[ -z "${rag_token}" ]]; then
      rag_token="${api_token}"
    fi
    if [[ -z "${copilot_key}" ]]; then
      copilot_key="${api_token}"
    fi

    if [[ -n "${api_token}" ]]; then
      auth_header_cag=(-H "X-API-Key: ${api_token}")
    fi
    if [[ -n "${rag_token}" ]]; then
      auth_header_rag=(-H "X-API-Key: ${rag_token}")
    fi
    if [[ -n "${copilot_key}" ]]; then
      auth_header_copilot=(-H "X-API-Key: ${copilot_key}")
    fi
  fi

  log "Running HTTP smoke checks..."
  check_http_code \
    "cag-service" \
    "200,400,401,403,422" \
    -X POST "http://127.0.0.1:8000/cag-answer" \
    -H "Content-Type: application/json" \
    "${auth_header_cag[@]}" \
    --data '{"question":"health check"}'

  check_http_code \
    "rag-service" \
    "400,401,403,404,422" \
    -X POST "http://127.0.0.1:8100/build" \
    -H "Content-Type: application/json" \
    "${auth_header_rag[@]}" \
    --data '{}'

  check_http_code \
    "copilot-service" \
    "401,403,422" \
    -X POST "http://127.0.0.1:9010/copilot/chat" \
    -H "Content-Type: application/json" \
    "${auth_header_copilot[@]}" \
    --data '{}'

  check_http_code \
    "copilot-frontend" \
    "200,301,302,307,308" \
    "http://127.0.0.1:3000/"

  log "Validation complete: backend services are reachable."
}

main "$@"
