#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
ROLE_ID_FILE="${ROOT_DIR}/docker/vault/runtime/role_id"
SECRET_ID_FILE="${ROOT_DIR}/docker/vault/runtime/secret_id"
VAULT_CA_CERT="${VAULT_CA_CERT:-${ROOT_DIR}/docker/internal-tls/vault-ca.pem}"
VAULT_LOCAL_ADDR="${VAULT_LOCAL_ADDR:-https://127.0.0.1:8200}"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

env_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' "${ENV_FILE}" 2>/dev/null || true
}

is_placeholder_value() {
  local value="$1"
  [[ -z "${value}" ]] && return 0
  [[ "${value}" == your-* ]] && return 0
  [[ "${value}" == "<"* ]] && return 0
  [[ "${value}" == sk-REPLACE_ME* ]] && return 0
  return 1
}

vault_api_unauth() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local code_file="${TMPDIR}/code"
  local body_file="${TMPDIR}/body"
  local -a args

  args=(-sS -m "${VAULT_REQUEST_TIMEOUT:-15}" -o "${body_file}" -w '%{http_code}' -X "${method}")
  if [[ -f "${VAULT_CA_CERT}" ]]; then
    args+=(--cacert "${VAULT_CA_CERT}")
  else
    args+=(-k)
  fi
  if [[ -n "${body}" ]]; then
    args+=(-H 'Content-Type: application/json' --data "${body}")
  fi

  if ! curl "${args[@]}" "${VAULT_LOCAL_ADDR%/}/v1/${path}" > "${code_file}"; then
    return 1
  fi

  VAULT_LAST_CODE="$(cat "${code_file}")"
  VAULT_LAST_BODY="$(cat "${body_file}" 2>/dev/null || true)"
  return 0
}

vault_api_auth() {
  local token="$1"
  local method="$2"
  local path="$3"
  local body="${4:-}"
  local code_file="${TMPDIR}/code"
  local body_file="${TMPDIR}/body"
  local -a args

  args=(-sS -m "${VAULT_REQUEST_TIMEOUT:-15}" -o "${body_file}" -w '%{http_code}' -X "${method}" -H "X-Vault-Token: ${token}")
  if [[ -f "${VAULT_CA_CERT}" ]]; then
    args+=(--cacert "${VAULT_CA_CERT}")
  else
    args+=(-k)
  fi
  if [[ -n "${body}" ]]; then
    args+=(-H 'Content-Type: application/json' --data "${body}")
  fi

  if ! curl "${args[@]}" "${VAULT_LOCAL_ADDR%/}/v1/${path}" > "${code_file}"; then
    return 1
  fi

  VAULT_LAST_CODE="$(cat "${code_file}")"
  VAULT_LAST_BODY="$(cat "${body_file}" 2>/dev/null || true)"
  return 0
}

assert_http_code() {
  local name="$1"
  local actual="$2"
  local expected_csv="$3"
  local expected

  IFS=',' read -r -a expected_codes <<< "${expected_csv}"
  for expected in "${expected_codes[@]}"; do
    if [[ "${actual}" == "${expected}" ]]; then
      log "${name}: HTTP ${actual} (acceptable)"
      return 0
    fi
  done

  echo "--- ${name} response body ---" >&2
  printf '%s\n' "${VAULT_LAST_BODY}" | sed -n '1,80p' >&2
  die "${name}: expected one of [${expected_csv}], got ${actual}"
}

main() {
  require_cmd jq
  require_cmd curl
  require_cmd docker

  [[ -f "${ENV_FILE}" ]] || die "Missing .env"

  local auth_method
  auth_method="$(env_value VAULT_AUTH_METHOD)"
  if [[ -z "${auth_method}" ]]; then
    auth_method="token"
  fi

  if [[ "${auth_method}" != "approle" ]]; then
    log "VAULT_AUTH_METHOD=${auth_method}; skipping AppRole workflow validation."
    exit 0
  fi

  [[ -s "${ROLE_ID_FILE}" ]] || die "Missing AppRole role_id file: ${ROLE_ID_FILE}"
  [[ -s "${SECRET_ID_FILE}" ]] || die "Missing AppRole secret_id file: ${SECRET_ID_FILE}"

  local role_id secret_id login_payload token lease_duration
  role_id="$(head -n1 "${ROLE_ID_FILE}" | tr -d '\r\n')"
  secret_id="$(head -n1 "${SECRET_ID_FILE}" | tr -d '\r\n')"
  [[ -n "${role_id}" && -n "${secret_id}" ]] || die "AppRole credential files are empty"

  log "Authenticating to Vault via AppRole..."
  login_payload="$(jq -cn --arg role_id "${role_id}" --arg secret_id "${secret_id}" '{role_id:$role_id, secret_id:$secret_id}')"
  vault_api_unauth POST auth/approle/login "${login_payload}" || die "Vault AppRole login request failed"
  assert_http_code "vault-approle-login" "${VAULT_LAST_CODE}" "200"

  token="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.auth.client_token // empty')"
  lease_duration="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.auth.lease_duration // 0')"
  [[ -n "${token}" ]] || die "Vault AppRole login did not return a client token"

  if [[ ! "${lease_duration}" =~ ^[0-9]+$ ]]; then
    die "Vault AppRole login returned non-numeric lease_duration"
  fi
  if (( lease_duration < 900 )); then
    die "Vault AppRole token lease_duration (${lease_duration}s) is too short for stable runtime operation"
  fi

  local policy
  if ! printf '%s' "${VAULT_LAST_BODY}" | jq -e '.auth.policies[] | select(. == "study-agents-runtime")' >/dev/null; then
    die "AppRole token is missing expected policy: study-agents-runtime"
  fi
  log "AppRole token includes expected policy and TTL guardrail."

  local required_pairs=(
    "OPENAI_API_KEY:kv/data/study-agents/openai"
    "SUPABASE_URL:kv/data/study-agents/supabase-url"
    "SUPABASE_KEY:kv/data/study-agents/supabase-key"
    "API_TOKEN:kv/data/study-agents/api-token"
    "RAG_API_TOKEN:kv/data/study-agents/rag-api-token"
    "COPILOT_API_KEY:kv/data/study-agents/copilot-api-key"
    "SCENARIO_API_KEY:kv/data/study-agents/scenario-api-key"
    "SCENARIO_SUPABASE_URL:kv/data/study-agents/scenario-supabase-url"
    "SCENARIO_SUPABASE_KEY:kv/data/study-agents/scenario-supabase-key"
  )

  local pair env_key secret_path env_raw secret_value
  for pair in "${required_pairs[@]}"; do
    env_key="${pair%%:*}"
    secret_path="${pair#*:}"
    env_raw="$(env_value "${env_key}")"

    # Only enforce existence for non-placeholder values that are expected to be in-use.
    if is_placeholder_value "${env_raw}"; then
      continue
    fi

    vault_api_auth "${token}" GET "${secret_path}" || die "Vault read failed for ${secret_path}"
    assert_http_code "vault-read-${env_key}" "${VAULT_LAST_CODE}" "200"

    secret_value="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
    [[ -n "${secret_value}" ]] || die "Vault secret ${secret_path} returned empty value"
  done

  # Runtime policy guardrails: must not be able to read/write outside approved scope.
  vault_api_auth "${token}" GET "kv/data/not-study-agents/probe" || die "Vault forbidden-read probe request failed"
  assert_http_code "vault-deny-read-outside-prefix" "${VAULT_LAST_CODE}" "403"

  vault_api_auth "${token}" POST "kv/data/study-agents/policy-write-probe" '{"data":{"value":"probe"}}' || die "Vault forbidden-write probe request failed"
  assert_http_code "vault-deny-write" "${VAULT_LAST_CODE}" "403"

  # Validate that runtime containers can execute AppRole flow and hydrate required envs.
  if docker compose ps --status running --services | grep -qx utility-service; then
    log "Validating in-container runtime secret hydration via use_env.sh..."
    if ! docker compose exec -T utility-service /bin/sh -lc 'rm -f /env/.vault_token.json /env/.env.runtime && /app/use_env.sh /bin/sh -lc "test -n \"$OPENAI_API_KEY\" && test -n \"$SUPABASE_URL\" && test -n \"$SUPABASE_KEY\""'; then
      die "Container runtime secret hydration failed (utility-service/use_env.sh)"
    fi
    log "Container runtime secret hydration succeeded."
  else
    log "utility-service is not running; skipped container runtime hydration probe."
  fi

  log "Vault workflow validation complete."
}

main "$@"
