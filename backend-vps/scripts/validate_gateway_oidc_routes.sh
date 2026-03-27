#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
PUBLIC_DOMAIN="${1:-}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' "${ENV_FILE}" 2>/dev/null || true
}

check_http_code() {
  local name="$1"
  local url="$2"
  local expected_csv="$3"
  local body_pattern="${4:-}"
  local status_file="${TMPDIR}/${name}.status"
  local body_file="${TMPDIR}/${name}.body"
  local -a curl_args=()
  local code expected

  if [[ -f "${ROOT_DIR}/docker/vault/bootstrap/caddy-root.crt" ]]; then
    curl_args=(--cacert "${ROOT_DIR}/docker/vault/bootstrap/caddy-root.crt")
  else
    curl_args=(-k)
  fi

  local retries interval attempt
  retries="${GATEWAY_ROUTE_RETRIES:-30}"
  interval="${GATEWAY_ROUTE_RETRY_INTERVAL:-2}"

  for attempt in $(seq 1 "${retries}"); do
    if ! curl -sS -m "${GATEWAY_ROUTE_TIMEOUT:-20}" -o "${body_file}" -w '%{http_code}' "${curl_args[@]}" "${url}" > "${status_file}"; then
      sleep "${interval}"
      continue
    fi

    code="$(cat "${status_file}")"
    IFS=',' read -r -a expected_codes <<< "${expected_csv}"
    for expected in "${expected_codes[@]}"; do
      if [[ "${code}" == "${expected}" ]]; then
        if [[ -n "${body_pattern}" ]] && ! rg -q --fixed-strings "${body_pattern}" "${body_file}"; then
          sleep "${interval}"
          continue 2
        fi
        log "${name}: HTTP ${code} (acceptable)"
        return 0
      fi
    done

    sleep "${interval}"
  done

  echo "--- ${name} body ---" >&2
  sed -n '1,80p' "${body_file}" >&2 || true
  die "${name}: expected one of [${expected_csv}], got ${code}"
}

main() {
  command -v curl >/dev/null 2>&1 || die "Missing required command: curl"
  command -v rg >/dev/null 2>&1 || die "Missing required command: rg"

  if [[ -z "${PUBLIC_DOMAIN}" ]]; then
    PUBLIC_DOMAIN="$(env_value PUBLIC_DOMAIN)"
  fi
  [[ -n "${PUBLIC_DOMAIN}" ]] || die "PUBLIC_DOMAIN is required (arg1 or .env)."

  local base
  base="https://${PUBLIC_DOMAIN}"

  log "Validating gateway Vault/OIDC routes at ${base} ..."

  # Vault UI/API routing.
  check_http_code "vault-ui" "${base}/ui/" "200"
  check_http_code "vault-health-proxy" "${base}/v1/sys/health" "200,429,472,473,501,503"

  # Authelia OIDC popup and frontend state routes must bypass forward-auth.
  check_http_code "authelia-consent" "${base}/consent/openid/decision?flow=route-check" "200"
  check_http_code "authelia-state" "${base}/api/state" "200" '"status":"OK"'
  check_http_code "authelia-manifest" "${base}/manifest.json" "200"

  log "Gateway route validation complete."
}

main "$@"
