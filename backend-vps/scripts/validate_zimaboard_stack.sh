#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

BASE_COMPOSE_FILE="${BASE_COMPOSE_FILE:-docker-compose.yml}"
ZIMA_COMPOSE_FILE="${ZIMA_COMPOSE_FILE:-docker-compose.zimaboard.yml}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

compose() {
  docker compose -f "${BASE_COMPOSE_FILE}" -f "${ZIMA_COMPOSE_FILE}" "$@"
}

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' .env 2>/dev/null || true
}

check_required_services() {
  local service
  local running
  running="$(compose ps --status running --services | tr '\n' ' ')"
  for service in cag-service rag-service copilot-service copilot-frontend redis authelia tls-gateway; do
    if [[ " ${running} " != *" ${service} "* ]]; then
      die "Required service is not running: ${service}"
    fi
  done
}

check_http_code() {
  local name="$1"
  local expected_csv="$2"
  shift 2

  local status_file="${TMPDIR}/${name}.status"
  local body_file="${TMPDIR}/${name}.body"
  local code

  if ! curl -sS -m 25 -o "${body_file}" -w '%{http_code}' "$@" > "${status_file}"; then
    die "${name}: HTTP request failed"
  fi

  code="$(cat "${status_file}")"
  IFS=',' read -r -a expected_codes <<< "${expected_csv}"
  for expected in "${expected_codes[@]}"; do
    if [[ "${code}" == "${expected}" ]]; then
      log "${name}: HTTP ${code} (acceptable)"
      return 0
    fi
  done

  echo "--- ${name} response body ---" >&2
  sed -n '1,60p' "${body_file}" >&2
  die "${name}: unexpected HTTP ${code}, expected one of [${expected_csv}]"
}

main() {
  [[ -f "${BASE_COMPOSE_FILE}" ]] || die "Missing ${BASE_COMPOSE_FILE}"
  [[ -f "${ZIMA_COMPOSE_FILE}" ]] || die "Missing ${ZIMA_COMPOSE_FILE}"
  [[ -f ".env" ]] || die "Missing .env"

  log "Validating compose configuration..."
  compose config -q

  log "Validating running services..."
  check_required_services
  compose ps

  local api_token copilot_key auth_header_cag=() auth_header_copilot=()
  api_token="$(env_value API_TOKEN)"
  copilot_key="$(env_value COPILOT_API_KEY)"
  if [[ -n "${api_token}" ]]; then
    auth_header_cag=(-H "X-API-Key: ${api_token}")
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
    "${auth_header_cag[@]}" \
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

  log "Validation complete: stack is reachable on local ports."
}

main "$@"
