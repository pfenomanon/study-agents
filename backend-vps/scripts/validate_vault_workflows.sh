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

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
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

  local openai_secret supabase_url_secret supabase_key_secret
  local api_token_secret rag_token_secret copilot_token_secret
  local scenario_api_secret scenario_url_secret scenario_key_secret

  vault_api_auth "${token}" GET "kv/data/study-agents/openai" || die "Vault read failed for kv/data/study-agents/openai"
  assert_http_code "vault-read-OPENAI_API_KEY" "${VAULT_LAST_CODE}" "200"
  openai_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
  [[ -n "${openai_secret}" ]] || die "Vault secret kv/data/study-agents/openai returned empty value"

  vault_api_auth "${token}" GET "kv/data/study-agents/supabase-url" || die "Vault read failed for kv/data/study-agents/supabase-url"
  assert_http_code "vault-read-SUPABASE_URL" "${VAULT_LAST_CODE}" "200"
  supabase_url_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
  [[ -n "${supabase_url_secret}" ]] || die "Vault secret kv/data/study-agents/supabase-url returned empty value"

  vault_api_auth "${token}" GET "kv/data/study-agents/supabase-key" || die "Vault read failed for kv/data/study-agents/supabase-key"
  assert_http_code "vault-read-SUPABASE_KEY" "${VAULT_LAST_CODE}" "200"
  supabase_key_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
  [[ -n "${supabase_key_secret}" ]] || die "Vault secret kv/data/study-agents/supabase-key returned empty value"

  vault_api_auth "${token}" GET "kv/data/study-agents/api-token" || die "Vault read failed for kv/data/study-agents/api-token"
  assert_http_code "vault-read-API_TOKEN" "${VAULT_LAST_CODE}" "200"
  api_token_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"

  vault_api_auth "${token}" GET "kv/data/study-agents/rag-api-token" || die "Vault read failed for kv/data/study-agents/rag-api-token"
  assert_http_code "vault-read-RAG_API_TOKEN" "${VAULT_LAST_CODE}" "200"
  rag_token_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"

  vault_api_auth "${token}" GET "kv/data/study-agents/copilot-api-key" || die "Vault read failed for kv/data/study-agents/copilot-api-key"
  assert_http_code "vault-read-COPILOT_API_KEY" "${VAULT_LAST_CODE}" "200"
  copilot_token_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"

  if token_required API_REQUIRE_TOKEN true && [[ -z "${api_token_secret}" ]]; then
    die "API_REQUIRE_TOKEN=true but kv/data/study-agents/api-token is empty."
  fi
  if token_required RAG_REQUIRE_TOKEN true && [[ -z "${rag_token_secret}" && -z "${api_token_secret}" ]]; then
    die "RAG_REQUIRE_TOKEN=true but both kv/data/study-agents/rag-api-token and kv/data/study-agents/api-token are empty."
  fi
  if token_required COPILOT_REQUIRE_TOKEN true && [[ -z "${copilot_token_secret}" && -z "${api_token_secret}" ]]; then
    die "COPILOT_REQUIRE_TOKEN=true but both kv/data/study-agents/copilot-api-key and kv/data/study-agents/api-token are empty."
  fi

  if ! is_placeholder_value "$(env_value SCENARIO_API_KEY)"; then
    vault_api_auth "${token}" GET "kv/data/study-agents/scenario-api-key" || die "Vault read failed for kv/data/study-agents/scenario-api-key"
    assert_http_code "vault-read-SCENARIO_API_KEY" "${VAULT_LAST_CODE}" "200"
    scenario_api_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
    [[ -n "${scenario_api_secret}" ]] || die "Vault secret kv/data/study-agents/scenario-api-key returned empty value"
  fi
  if ! is_placeholder_value "$(env_value SCENARIO_SUPABASE_URL)"; then
    vault_api_auth "${token}" GET "kv/data/study-agents/scenario-supabase-url" || die "Vault read failed for kv/data/study-agents/scenario-supabase-url"
    assert_http_code "vault-read-SCENARIO_SUPABASE_URL" "${VAULT_LAST_CODE}" "200"
    scenario_url_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
    [[ -n "${scenario_url_secret}" ]] || die "Vault secret kv/data/study-agents/scenario-supabase-url returned empty value"
  fi
  if ! is_placeholder_value "$(env_value SCENARIO_SUPABASE_KEY)"; then
    vault_api_auth "${token}" GET "kv/data/study-agents/scenario-supabase-key" || die "Vault read failed for kv/data/study-agents/scenario-supabase-key"
    assert_http_code "vault-read-SCENARIO_SUPABASE_KEY" "${VAULT_LAST_CODE}" "200"
    scenario_key_secret="$(printf '%s' "${VAULT_LAST_BODY}" | jq -r '.data.data.value // empty')"
    [[ -n "${scenario_key_secret}" ]] || die "Vault secret kv/data/study-agents/scenario-supabase-key returned empty value"
  fi

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
