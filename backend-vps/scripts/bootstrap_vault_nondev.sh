#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
INIT_DIR="${ROOT_DIR}/docker/vault/bootstrap"
RUNTIME_DIR="${ROOT_DIR}/docker/vault/runtime"
TLS_DIR="${ROOT_DIR}/docker/internal-tls"
INIT_FILE="${INIT_DIR}/init.json"
ROLE_ID_FILE="${RUNTIME_DIR}/role_id"
SECRET_ID_FILE="${RUNTIME_DIR}/secret_id"
CADDY_ROOT_CA="${INIT_DIR}/caddy-root.crt"
ENV_BACKUP_PATH=""

COMPOSE_FILES=("${ROOT_DIR}/docker-compose.yml")
if [[ -f "${ROOT_DIR}/docker-compose.zimaboard.yml" ]]; then
  COMPOSE_FILES+=("${ROOT_DIR}/docker-compose.zimaboard.yml")
fi

dc() {
  local args=()
  local file
  for file in "${COMPOSE_FILES[@]}"; do
    args+=( -f "${file}" )
  done
  COMPOSE_PROFILES="${COMPOSE_PROFILES:-vault}" docker compose "${args[@]}" "$@"
}

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

get_env() {
  local key="$1"
  [[ -f "${ENV_FILE}" ]] || return 0
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' "${ENV_FILE}"
}

set_env() {
  local key="$1"
  local value="$2"
  local tmp

  touch "${ENV_FILE}"
  tmp="$(mktemp)"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") { print key "=" value; updated = 1; next }
    { print }
    END {
      if (!updated) {
        print key "=" value
      }
    }
  ' "${ENV_FILE}" > "${tmp}"
  mv "${tmp}" "${ENV_FILE}"
}

backup_env_once() {
  if [[ -n "${ENV_BACKUP_PATH}" ]]; then
    return 0
  fi
  ENV_BACKUP_PATH="${INIT_DIR}/env-pre-vault-scrub-$(date -u +%Y%m%dT%H%M%SZ).bak"
  umask 077
  cp "${ENV_FILE}" "${ENV_BACKUP_PATH}"
  chmod 600 "${ENV_BACKUP_PATH}" || true
}

unset_env() {
  local key="$1"
  [[ -f "${ENV_FILE}" ]] || return 0
  local tmp
  tmp="$(mktemp)"
  awk -v key="${key}" '$0 !~ ("^" key "=") { print }' "${ENV_FILE}" > "${tmp}"
  mv "${tmp}" "${ENV_FILE}"
}

is_placeholder_value() {
  local value="$1"
  [[ -z "${value}" ]] && return 0
  [[ "${value}" == your-* ]] && return 0
  [[ "${value}" == "<"* ]] && return 0
  [[ "${value}" == sk-REPLACE_ME* ]] && return 0
  return 1
}

vault_cli() {
  dc exec -T vault env \
    VAULT_ADDR="https://127.0.0.1:8200" \
    VAULT_CACERT="/tls/vault-ca.pem" \
    "$@"
}

vault_root() {
  local token="$1"
  shift
  dc exec -T vault env \
    VAULT_ADDR="https://127.0.0.1:8200" \
    VAULT_CACERT="/tls/vault-ca.pem" \
    VAULT_TOKEN="${token}" \
    "$@"
}

write_vault_policy_from_host() {
  local token="$1"
  local policy_name="$2"
  local policy_file="$3"

  [[ -f "${policy_file}" ]] || die "Missing policy file: ${policy_file}"
  dc exec -T vault env \
    VAULT_ADDR="https://127.0.0.1:8200" \
    VAULT_CACERT="/tls/vault-ca.pem" \
    VAULT_TOKEN="${token}" \
    sh -lc "cat > /tmp/${policy_name}.hcl && vault policy write ${policy_name} /tmp/${policy_name}.hcl" < "${policy_file}"
}

ensure_gateway_cidr_contains_backend_subnet() {
  local project_name network_name backend_subnet cidrs

  project_name="${COMPOSE_PROJECT_NAME:-$(basename "${ROOT_DIR}")}"
  network_name="${project_name}_backend"

  if ! docker network inspect "${network_name}" >/dev/null 2>&1; then
    network_name="$(docker network ls --format '{{.Name}}' | awk '/_backend$/ {print; exit}')"
  fi

  if [[ -z "${network_name}" ]]; then
    return 0
  fi

  backend_subnet="$(docker network inspect "${network_name}" --format '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || true)"
  if [[ -z "${backend_subnet}" ]]; then
    return 0
  fi

  cidrs="$(get_env GATEWAY_ALLOWED_CIDRS)"
  if [[ -z "${cidrs}" ]]; then
    cidrs="127.0.0.1/32 ::1/128"
  fi

  if [[ " ${cidrs} " != *" ${backend_subnet} "* ]]; then
    set_env GATEWAY_ALLOWED_CIDRS "${cidrs} ${backend_subnet}"
    log "Added Docker backend subnet to GATEWAY_ALLOWED_CIDRS: ${backend_subnet}"
  fi
}

sync_env_secret_to_vault() {
  local token="$1"
  local env_key="$2"
  local vault_path="$3"
  local value

  value="$(get_env "${env_key}")"
  if is_placeholder_value "${value}"; then
    return 0
  fi

  vault_root "${token}" vault kv put "${vault_path}" "value=${value}" >/dev/null
  log "Synced ${env_key} -> ${vault_path}"
}

scrub_env_secret_for_vault_first() {
  local env_key="$1"
  if [[ -z "$(get_env "${env_key}")" ]]; then
    return 0
  fi
  backup_env_once
  set_env "${env_key}" ""
  log "Removed plaintext ${env_key} from .env (Vault-first mode)."
}

main() {
  require_cmd docker
  require_cmd jq
  require_cmd curl
  require_cmd openssl

  if [[ ! -f "${ENV_FILE}" ]]; then
    [[ -f "${ROOT_DIR}/.env.example" ]] || die "Missing .env and .env.example"
    cp "${ROOT_DIR}/.env.example" "${ENV_FILE}"
    log "Created .env from .env.example"
  fi

  mkdir -p "${INIT_DIR}" "${RUNTIME_DIR}" "${ROOT_DIR}/docker/vault/data"
  chmod 700 "${INIT_DIR}" || true
  chmod 755 "${RUNTIME_DIR}" || true

  bash "${ROOT_DIR}/scripts/bootstrap_internal_tls.sh"

  set_env VAULT_ADDR "https://vault:8200"
  set_env VAULT_CACERT "/vault/tls/vault-ca.pem"
  set_env VAULT_AUTH_METHOD "approle"
  set_env VAULT_ROLE_ID_FILE "/vault/bootstrap/role_id"
  set_env VAULT_SECRET_ID_FILE "/vault/bootstrap/secret_id"
  set_env ALLOW_PLAINTEXT_ENV_SECRETS "false"
  set_env VAULT_SCRUB_ENV_SECRETS "true"
  unset_env VAULT_TOKEN
  unset_env VAULT_DEV_ROOT_TOKEN_ID

  log "Starting Vault in non-dev mode..."
  dc up -d vault

  log "Waiting for Vault listener..."
  local i code
  for i in $(seq 1 45); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --cacert "${TLS_DIR}/vault-ca.pem" https://127.0.0.1:8200/v1/sys/health || true)"
    if [[ -n "${code}" && "${code}" != "000" ]]; then
      break
    fi
    sleep 2
  done

  local status_json initialized sealed
  status_json="$(vault_cli vault status -format=json 2>/dev/null || true)"
  initialized="$(printf '%s' "${status_json}" | jq -r 'if .initialized == null then "false" else (.initialized|tostring) end')"
  sealed="$(printf '%s' "${status_json}" | jq -r 'if .sealed == null then "true" else (.sealed|tostring) end')"

  if [[ "${initialized}" != "true" ]]; then
    log "Initializing Vault..."
    local init_json
    init_json="$(vault_cli vault operator init -format=json)"
    umask 077
    printf '%s\n' "${init_json}" > "${INIT_FILE}"
    chmod 600 "${INIT_FILE}" || true
    status_json="$(vault_cli vault status -format=json 2>/dev/null || true)"
    sealed="$(printf '%s' "${status_json}" | jq -r 'if .sealed == null then "true" else (.sealed|tostring) end')"
  elif [[ ! -f "${INIT_FILE}" ]]; then
    log "Vault is already initialized; init material not found at ${INIT_FILE}."
  fi

  if [[ "${sealed}" == "true" ]]; then
    [[ -f "${INIT_FILE}" ]] || die "Vault is sealed and ${INIT_FILE} is missing. Cannot auto-unseal."
    local unseal_key attempt
    log "Unsealing Vault..."
    for attempt in $(seq 1 5); do
      while IFS= read -r unseal_key; do
        [[ -n "${unseal_key}" ]] || continue
        vault_cli vault operator unseal "${unseal_key}" >/dev/null || true
      done < <(jq -r '.unseal_keys_b64[] // empty' "${INIT_FILE}")

      status_json="$(vault_cli vault status -format=json 2>/dev/null || true)"
      sealed="$(printf '%s' "${status_json}" | jq -r 'if .sealed == null then "true" else (.sealed|tostring) end')"
      if [[ "${sealed}" == "false" ]]; then
        break
      fi
      sleep 2
    done

    [[ "${sealed}" == "false" ]] || die "Vault remains sealed after unseal attempt."
  fi

  [[ -f "${INIT_FILE}" ]] || die "Missing ${INIT_FILE}; cannot continue with policy/auth bootstrap."
  local root_token
  root_token="$(jq -r '.root_token // empty' "${INIT_FILE}")"
  [[ -n "${root_token}" ]] || die "Unable to read root token from ${INIT_FILE}"

  log "Configuring Vault secrets engine and policies..."
  if ! vault_root "${root_token}" vault secrets list -format=json | jq -e 'has("kv/")' >/dev/null; then
    vault_root "${root_token}" vault secrets enable -path=kv kv-v2 >/dev/null
  fi

  write_vault_policy_from_host "${root_token}" study-agents-runtime "${ROOT_DIR}/docker/vault/policies/study-agents-runtime.hcl" >/dev/null
  write_vault_policy_from_host "${root_token}" vault-admin "${ROOT_DIR}/docker/vault/policies/vault-admin.hcl" >/dev/null

  if ! vault_root "${root_token}" vault auth list -format=json | jq -e 'has("approle/")' >/dev/null; then
    vault_root "${root_token}" vault auth enable approle >/dev/null
  fi

  vault_root "${root_token}" vault write auth/approle/role/study-agents-runtime \
    token_policies="study-agents-runtime" \
    token_ttl="1h" \
    token_max_ttl="4h" \
    secret_id_ttl="24h" \
    secret_id_num_uses="0" >/dev/null

  local role_id secret_id
  role_id="$(vault_root "${root_token}" vault read -field=role_id auth/approle/role/study-agents-runtime/role-id | tr -d '\r\n')"
  secret_id="$(vault_root "${root_token}" vault write -f -field=secret_id auth/approle/role/study-agents-runtime/secret-id | tr -d '\r\n')"

  umask 077
  printf '%s\n' "${role_id}" > "${ROLE_ID_FILE}"
  printf '%s\n' "${secret_id}" > "${SECRET_ID_FILE}"
  chmod 644 "${ROLE_ID_FILE}" "${SECRET_ID_FILE}" || true

  log "Syncing non-placeholder secrets from .env to Vault..."
  sync_env_secret_to_vault "${root_token}" OPENAI_API_KEY kv/study-agents/openai
  sync_env_secret_to_vault "${root_token}" SUPABASE_URL kv/study-agents/supabase-url
  sync_env_secret_to_vault "${root_token}" SUPABASE_KEY kv/study-agents/supabase-key
  sync_env_secret_to_vault "${root_token}" API_TOKEN kv/study-agents/api-token
  sync_env_secret_to_vault "${root_token}" RAG_API_TOKEN kv/study-agents/rag-api-token
  sync_env_secret_to_vault "${root_token}" SCENARIO_API_KEY kv/study-agents/scenario-api-key
  sync_env_secret_to_vault "${root_token}" COPILOT_API_KEY kv/study-agents/copilot-api-key
  sync_env_secret_to_vault "${root_token}" SCENARIO_SUPABASE_URL kv/study-agents/scenario-supabase-url
  sync_env_secret_to_vault "${root_token}" SCENARIO_SUPABASE_KEY kv/study-agents/scenario-supabase-key

  if is_true "${VAULT_SCRUB_ENV_SECRETS:-true}"; then
    log "Scrubbing plaintext runtime secrets from .env (backup retained)..."
    scrub_env_secret_for_vault_first OPENAI_API_KEY
    scrub_env_secret_for_vault_first SUPABASE_URL
    scrub_env_secret_for_vault_first SUPABASE_KEY
    scrub_env_secret_for_vault_first API_TOKEN
    scrub_env_secret_for_vault_first RAG_API_TOKEN
    scrub_env_secret_for_vault_first SCENARIO_API_KEY
    scrub_env_secret_for_vault_first COPILOT_API_KEY
    scrub_env_secret_for_vault_first SCENARIO_SUPABASE_URL
    scrub_env_secret_for_vault_first SCENARIO_SUPABASE_KEY
  else
    log "Keeping plaintext .env secrets (VAULT_SCRUB_ENV_SECRETS=${VAULT_SCRUB_ENV_SECRETS:-unset})."
  fi

  local public_domain vault_oidc_client_id vault_oidc_client_secret vault_oidc_redirect
  public_domain="$(get_env PUBLIC_DOMAIN)"

  if [[ -n "${public_domain}" ]]; then
    vault_oidc_client_id="$(get_env AUTHELIA_VAULT_OIDC_CLIENT_ID)"
    if [[ -z "${vault_oidc_client_id}" ]]; then
      vault_oidc_client_id="vault"
      set_env AUTHELIA_VAULT_OIDC_CLIENT_ID "${vault_oidc_client_id}"
    fi

    vault_oidc_client_secret="$(get_env AUTHELIA_VAULT_OIDC_CLIENT_SECRET)"
    if [[ -z "${vault_oidc_client_secret}" ]]; then
      vault_oidc_client_secret="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-24)"
      set_env AUTHELIA_VAULT_OIDC_CLIENT_SECRET "${vault_oidc_client_secret}"
    fi

    vault_oidc_redirect="$(get_env AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI)"
    if [[ -z "${vault_oidc_redirect}" ]]; then
      vault_oidc_redirect="https://${public_domain}/ui/vault/auth/oidc/oidc/callback"
      set_env AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI "${vault_oidc_redirect}"
    fi

    if [[ -z "$(get_env AUTHELIA_USERS_SOURCE)" ]]; then
      set_env AUTHELIA_USERS_SOURCE file
    fi

    ensure_gateway_cidr_contains_backend_subnet

    log "Bootstrapping Authelia configuration for Vault OIDC client..."
    bash "${ROOT_DIR}/scripts/bootstrap_authelia.sh"

    log "Recreating auth gateway services..."
    dc up -d --force-recreate authelia tls-gateway

    log "Exporting Caddy local root CA for Vault OIDC discovery trust..."
    bash "${ROOT_DIR}/scripts/export_caddy_root_ca.sh" "${CADDY_ROOT_CA}"

    if is_true "${GATEWAY_ROUTE_VALIDATE:-true}"; then
      log "Validating gateway Vault/OIDC popup routes..."
      bash "${ROOT_DIR}/scripts/validate_gateway_oidc_routes.sh" "${public_domain}"
    else
      log "Skipping gateway route validation (GATEWAY_ROUTE_VALIDATE=${GATEWAY_ROUTE_VALIDATE:-unset})."
    fi

    if ! vault_root "${root_token}" vault auth list -format=json | jq -e 'has("oidc/")' >/dev/null; then
      vault_root "${root_token}" vault auth enable oidc >/dev/null
    fi

    log "Configuring Vault OIDC auth against Authelia..."
    vault_root "${root_token}" vault write auth/oidc/config \
      oidc_discovery_url="https://${public_domain}" \
      oidc_discovery_ca_pem=@/vault/bootstrap/caddy-root.crt \
      oidc_client_id="${vault_oidc_client_id}" \
      oidc_client_secret="${vault_oidc_client_secret}" \
      default_role="vault-admin" \
      bound_issuer="https://${public_domain}" >/dev/null

    vault_root "${root_token}" vault write auth/oidc/role/vault-admin \
      user_claim="preferred_username" \
      groups_claim="groups" \
      oidc_scopes="openid,profile,email,groups" \
      token_policies="vault-admin" \
      allowed_redirect_uris="${vault_oidc_redirect}" \
      allowed_redirect_uris="http://localhost:8250/oidc/callback" >/dev/null
  else
    log "PUBLIC_DOMAIN is not set; skipping Vault OIDC setup."
  fi

  log "Recreating runtime services to pick up AppRole secret mounts..."
  dc up -d --force-recreate cag-service rag-service utility-service copilot-service

  echo
  echo "Vault non-dev bootstrap complete."
  echo "  init material: ${INIT_FILE}"
  echo "  approle role_id: ${ROLE_ID_FILE}"
  echo "  approle secret_id: ${SECRET_ID_FILE}"
  echo "  VAULT_ADDR in .env: $(get_env VAULT_ADDR)"
  echo "  VAULT_AUTH_METHOD in .env: $(get_env VAULT_AUTH_METHOD)"
  if [[ -n "${ENV_BACKUP_PATH}" ]]; then
    echo "  pre-scrub env backup: ${ENV_BACKUP_PATH}"
  fi
  if [[ -n "${public_domain}" ]]; then
    echo "  Vault UI OIDC login: https://${public_domain}/ui/"
    echo "  Vault UI OIDC fields: Method=OIDC, Role=vault-admin, Mount path=oidc"
    echo "  Route recheck: bash scripts/validate_gateway_oidc_routes.sh ${public_domain}"
  fi
}

main "$@"
