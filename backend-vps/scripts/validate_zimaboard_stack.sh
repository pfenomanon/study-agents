#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

BASE_COMPOSE_FILE="${BASE_COMPOSE_FILE:-docker-compose.yml}"
ZIMA_COMPOSE_FILE="${ZIMA_COMPOSE_FILE:-docker-compose.zimaboard.yml}"
TLS_CA_CERT="${TLS_CA_CERT:-${ROOT_DIR}/docker/internal-tls/internal-ca.crt}"
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

  if ! curl -sS -m 25 --cacert "${TLS_CA_CERT}" -o "${body_file}" -w '%{http_code}' "$@" > "${status_file}"; then
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

resolve_caddy_volume() {
  if docker volume inspect backend-vps_caddy-data >/dev/null 2>&1; then
    printf '%s' "backend-vps_caddy-data"
    return 0
  fi
  docker volume ls --format '{{.Name}}' | awk '/_caddy-data$/ {print; exit}'
}

check_gateway_tls_chain() {
  local caddy_volume public_domain root_ca intermediate_ca sclient_out cert_count gateway_code
  caddy_volume="$(resolve_caddy_volume)"
  [[ -n "${caddy_volume}" ]] || die "tls-gateway is running but Caddy data volume was not found."

  root_ca="${TMPDIR}/caddy-local-root.crt"
  intermediate_ca="${TMPDIR}/caddy-local-intermediate.crt"
  sclient_out="${TMPDIR}/gateway.sclient.txt"

  docker run --rm -v "${caddy_volume}:/data:ro" alpine \
    sh -lc "cat /data/caddy/pki/authorities/local/root.crt" > "${root_ca}"
  docker run --rm -v "${caddy_volume}:/data:ro" alpine \
    sh -lc "cat /data/caddy/pki/authorities/local/intermediate.crt" > "${intermediate_ca}"
  [[ -s "${root_ca}" ]] || die "Failed to read Caddy root CA from volume ${caddy_volume}."
  [[ -s "${intermediate_ca}" ]] || die "Failed to read Caddy intermediate CA from volume ${caddy_volume}."

  openssl verify -CAfile "${root_ca}" "${intermediate_ca}" >/dev/null || die "Caddy intermediate does not verify against Caddy root."

  public_domain="$(env_value PUBLIC_DOMAIN)"
  if [[ -z "${public_domain}" ]]; then
    public_domain="127.0.0.1"
  fi

  if ! openssl s_client \
      -connect 127.0.0.1:443 \
      -servername "${public_domain}" \
      -verify_return_error \
      -CAfile "${root_ca}" \
      -showcerts \
      < /dev/null > "${sclient_out}" 2>&1; then
    echo "--- tls-gateway openssl output ---" >&2
    sed -n '1,120p' "${sclient_out}" >&2 || true
    die "tls-gateway certificate verification failed for SNI ${public_domain}."
  fi

  cert_count="$(grep -c "BEGIN CERTIFICATE" "${sclient_out}" || true)"
  if (( cert_count < 2 )); then
    die "tls-gateway did not present a full chain (expected leaf + intermediate, found ${cert_count})."
  fi
  log "tls-gateway: certificate chain depth is ${cert_count} cert(s) (includes intermediate)."

  gateway_code="$(
    curl -sS -m 25 --cacert "${root_ca}" \
      --resolve "${public_domain}:443:127.0.0.1" \
      -o "${TMPDIR}/gateway.body" \
      -w '%{http_code}' \
      "https://${public_domain}/healthz" || true
  )"
  if [[ "${gateway_code}" != "200" ]]; then
    echo "--- tls-gateway /healthz body ---" >&2
    sed -n '1,80p' "${TMPDIR}/gateway.body" >&2 || true
    die "tls-gateway /healthz check failed with HTTP ${gateway_code}."
  fi
  log "tls-gateway: /healthz returned HTTP 200 using exported Caddy root trust."
}

main() {
  [[ -f "${BASE_COMPOSE_FILE}" ]] || die "Missing ${BASE_COMPOSE_FILE}"
  [[ -f "${ZIMA_COMPOSE_FILE}" ]] || die "Missing ${ZIMA_COMPOSE_FILE}"
  [[ -f ".env" ]] || die "Missing .env"
  [[ -f "${TLS_CA_CERT}" ]] || die "Missing TLS CA certificate: ${TLS_CA_CERT}"
  command -v openssl >/dev/null 2>&1 || die "openssl is not installed"

  log "Validating compose configuration..."
  compose config -q

  log "Validating running services..."
  check_required_services
  compose ps

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

  log "Running HTTPS smoke checks..."
  check_http_code \
    "cag-service" \
    "200,400,401,403,422" \
    -X POST "https://127.0.0.1:8000/cag-answer" \
    -H "Content-Type: application/json" \
    "${auth_header_cag[@]}" \
    --data '{"question":"health check"}'

  check_http_code \
    "rag-service" \
    "400,401,403,404,422" \
    -X POST "https://127.0.0.1:8100/build" \
    -H "Content-Type: application/json" \
    "${auth_header_rag[@]}" \
    --data '{}'

  check_http_code \
    "copilot-service" \
    "401,403,422" \
    -X POST "https://127.0.0.1:9010/copilot/chat" \
    -H "Content-Type: application/json" \
    "${auth_header_copilot[@]}" \
    --data '{}'

  check_http_code \
    "copilot-frontend" \
    "200,301,302,307,308" \
    "https://127.0.0.1:3000/"

  log "Validating gateway certificate chain/trust..."
  check_gateway_tls_chain

  log "Validation complete: stack is reachable on local ports."
}

main "$@"
