#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

WAIT_RETRIES="${WAIT_RETRIES:-45}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
TLS_CA_CERT="${TLS_CA_CERT:-${ROOT_DIR}/docker/internal-tls/internal-ca.crt}"
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
    if ! curl -sS -m 25 --cacert "${TLS_CA_CERT}" -o "${body_file}" -w '%{http_code}' "$@" > "${status_file}"; then
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
  [[ -f docker-compose.yml ]] || die "Missing docker-compose.yml"
  [[ -f .env ]] || die "Missing .env"
  [[ -f "${TLS_CA_CERT}" ]] || die "Missing TLS CA certificate: ${TLS_CA_CERT}"
  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin is not available"
  command -v openssl >/dev/null 2>&1 || die "openssl is not installed"

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

  if dc ps --status running --services | grep -qx vault; then
    local vault_ca_cert="${ROOT_DIR}/docker/internal-tls/vault-ca.pem"
    [[ -f "${vault_ca_cert}" ]] || die "vault is running but CA certificate is missing: ${vault_ca_cert}"

    local vault_status_file="${TMPDIR}/vault.status"
    local vault_body_file="${TMPDIR}/vault.body"
    local vault_code
    vault_code="$(
      curl -sS -m 25 --cacert "${vault_ca_cert}" \
        -o "${vault_body_file}" \
        -w '%{http_code}' \
        "https://127.0.0.1:8200/v1/sys/health" || true
    )"
    case "${vault_code}" in
      200|429|472|473|501|503)
        log "vault: HTTPS health HTTP ${vault_code} (acceptable)"
        ;;
      *)
        echo "--- vault HTTPS response body ---" >&2
        sed -n '1,80p' "${vault_body_file}" >&2 || true
        die "vault: unexpected HTTPS status ${vault_code}"
        ;;
    esac

    vault_code="$(
      curl -sS -m 25 \
        -o "${vault_body_file}" \
        -w '%{http_code}' \
        "http://127.0.0.1:8200/v1/sys/health" || true
    )"
    if [[ "${vault_code}" != "400" ]]; then
      echo "--- vault HTTP response body ---" >&2
      sed -n '1,80p' "${vault_body_file}" >&2 || true
      die "vault: expected HTTP 400 over plaintext, got ${vault_code}"
    fi
    log "vault: plaintext HTTP rejected with 400 (expected)"
  fi

  log "Validation complete: backend services are reachable."
}

main "$@"
