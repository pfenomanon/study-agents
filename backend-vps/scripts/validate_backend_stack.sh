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
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' .env 2>/dev/null || true
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

main() {
  [[ -f docker-compose.yml ]] || die "Missing docker-compose.yml"
  [[ -f .env ]] || die "Missing .env"
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin is not available"

  log "Validating compose syntax..."
  dc config -q

  log "Waiting for required services to become healthy..."
  wait_for_required_services

  local api_token rag_token copilot_key auth_header_cag=() auth_header_rag=() auth_header_copilot=()
  api_token="$(env_value API_TOKEN)"
  rag_token="$(env_value RAG_API_TOKEN)"
  copilot_key="$(env_value COPILOT_API_KEY)"

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
