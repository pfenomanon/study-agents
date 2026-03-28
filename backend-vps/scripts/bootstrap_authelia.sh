#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
AUTHELIA_DIR="${ROOT_DIR}/docker/authelia"
AUTHELIA_RUNTIME_DIR="${AUTHELIA_DIR}/runtime"
AUTHELIA_CONFIG="${AUTHELIA_DIR}/configuration.yml"
AUTHELIA_USERS="${AUTHELIA_DIR}/users_database.yml"
AUTHELIA_OIDC_JWKS_KEY_PATH="${AUTHELIA_DIR}/oidc_jwks_rs256.pem"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo ".env file not found at ${ENV_FILE}" >&2
  exit 1
fi

ensure_authelia_dir_writable() {
  mkdir -p "${AUTHELIA_DIR}" "${AUTHELIA_RUNTIME_DIR}"
  if touch "${AUTHELIA_DIR}/.perm_check" 2>/dev/null; then
    touch "${AUTHELIA_RUNTIME_DIR}/.perm_check"
    rm -f "${AUTHELIA_DIR}/.perm_check"
    rm -f "${AUTHELIA_RUNTIME_DIR}/.perm_check"
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    echo "Authelia directory is not writable; attempting ownership repair with sudo..."
    sudo chown -R "$(id -u):$(id -g)" "${AUTHELIA_DIR}"
    touch "${AUTHELIA_DIR}/.perm_check"
    touch "${AUTHELIA_RUNTIME_DIR}/.perm_check"
    rm -f "${AUTHELIA_DIR}/.perm_check"
    rm -f "${AUTHELIA_RUNTIME_DIR}/.perm_check"
    return 0
  fi

  echo "Authelia directory is not writable and sudo is unavailable: ${AUTHELIA_DIR}" >&2
  exit 1
}

ensure_authelia_dir_writable

bash "${ROOT_DIR}/scripts/bootstrap_internal_tls.sh"

get_env() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, length(key) + 2); exit}' "${ENV_FILE}"
}

set_env() {
  local key="$1"
  local value="$2"
  local tmp
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

ensure_path_owner() {
  local owner="$1"
  local target="$2"
  if chown "${owner}" "${target}" 2>/dev/null; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo chown "${owner}" "${target}" >/dev/null 2>&1 || true
  fi
}

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

COMPOSE_FILES=("${ROOT_DIR}/docker-compose.yml")
if [[ -f "${ROOT_DIR}/docker-compose.zimaboard.yml" ]]; then
  COMPOSE_FILES+=("${ROOT_DIR}/docker-compose.zimaboard.yml")
fi

dc() {
  local args=()
  local file
  for file in "${COMPOSE_FILES[@]}"; do
    args+=(-f "${file}")
  done
  docker compose "${args[@]}" "$@"
}

resolve_approle_file_path() {
  local path_value="$1"
  if [[ -z "${path_value}" || "${path_value}" == "/vault/bootstrap/role_id" ]]; then
    echo "${ROOT_DIR}/docker/vault/runtime/role_id"
    return
  fi
  if [[ "${path_value}" == "/vault/bootstrap/secret_id" ]]; then
    echo "${ROOT_DIR}/docker/vault/runtime/secret_id"
    return
  fi
  if [[ "${path_value}" = /* ]]; then
    echo "${path_value}"
    return
  fi
  echo "${ROOT_DIR}/${path_value}"
}

VAULT_APPROLE_TOKEN=""
vault_access_ready=0
vault_first_mode=0

init_vault_access() {
  local auth_method allow_plain role_id_file secret_id_file role_id secret_id
  local token

  auth_method="$(get_env VAULT_AUTH_METHOD)"
  [[ -n "${auth_method}" ]] || auth_method="token"
  allow_plain="$(get_env ALLOW_PLAINTEXT_ENV_SECRETS)"

  if [[ "${auth_method}" == "approle" ]] && ! is_true "${allow_plain:-false}"; then
    vault_first_mode=1
  fi

  [[ "${auth_method}" == "approle" ]] || return 0

  role_id_file="$(resolve_approle_file_path "$(get_env VAULT_ROLE_ID_FILE)")"
  secret_id_file="$(resolve_approle_file_path "$(get_env VAULT_SECRET_ID_FILE)")"
  [[ -r "${role_id_file}" && -r "${secret_id_file}" ]] || return 0

  if ! dc ps --status running --services 2>/dev/null | grep -qx vault; then
    return 0
  fi

  role_id="$(head -n1 "${role_id_file}" | tr -d '\r\n')"
  secret_id="$(head -n1 "${secret_id_file}" | tr -d '\r\n')"
  [[ -n "${role_id}" && -n "${secret_id}" ]] || return 0

  token="$(
    dc exec -T vault env \
      VAULT_ADDR="https://127.0.0.1:8200" \
      VAULT_CACERT="/tls/vault-ca.pem" \
      ROLE_ID="${role_id}" \
      SECRET_ID="${secret_id}" \
      sh -lc 'vault write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID"' \
      2>/dev/null | tr -d '\r\n'
  )"
  if [[ -n "${token}" ]]; then
    VAULT_APPROLE_TOKEN="${token}"
    vault_access_ready=1
  fi
}

vault_kv_get_value() {
  local path="$1"
  [[ "${vault_access_ready}" -eq 1 ]] || return 1
  dc exec -T vault env \
    VAULT_ADDR="https://127.0.0.1:8200" \
    VAULT_CACERT="/tls/vault-ca.pem" \
    VAULT_TOKEN="${VAULT_APPROLE_TOKEN}" \
    vault kv get -field=value "${path}" 2>/dev/null | tr -d '\r\n'
}

vault_kv_put_value() {
  local path="$1"
  local value="$2"
  [[ "${vault_access_ready}" -eq 1 ]] || return 1
  dc exec -T vault env \
    VAULT_ADDR="https://127.0.0.1:8200" \
    VAULT_CACERT="/tls/vault-ca.pem" \
    VAULT_TOKEN="${VAULT_APPROLE_TOKEN}" \
    vault kv put "${path}" "value=${value}" >/dev/null 2>&1
}

set_env_secret_if_allowed() {
  local key="$1"
  local value="$2"
  if [[ -z "$(get_env "${key}")" ]] && { [[ "${vault_first_mode}" -eq 0 ]] || [[ "${vault_access_ready}" -eq 0 ]]; }; then
    set_env "${key}" "${value}"
  fi
}

resolve_secret_with_vault() {
  local env_key="$1"
  local vault_path="$2"
  local generator="${3:-}"
  local value

  value="$(vault_kv_get_value "${vault_path}" || true)"
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi

  value="$(get_env "${env_key}")"
  if [[ -z "${value}" ]]; then
    case "${generator}" in
      rand_hex_32)
        value="$(rand_hex 32)"
        ;;
      rand_password)
        value="$(rand_password)"
        ;;
      default_gateway_admin)
        value="gateway-admin"
        ;;
    esac
  fi

  if [[ -n "${value}" ]]; then
    vault_kv_put_value "${vault_path}" "${value}" || true
    set_env_secret_if_allowed "${env_key}" "${value}"
  fi

  printf '%s' "${value}"
}

rand_hex() {
  local bytes="$1"
  openssl rand -hex "${bytes}"
}

rand_password() {
  openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-24
}

users_file_has_entries() {
  [[ -f "${AUTHELIA_USERS}" ]] || return 1
  grep -Eq '^  [A-Za-z0-9._@-]+:' "${AUTHELIA_USERS}"
}

init_vault_access

AUTHELIA_CONTAINER_UID="$(get_env AUTHELIA_CONTAINER_UID)"
AUTHELIA_CONTAINER_GID="$(get_env AUTHELIA_CONTAINER_GID)"
if [[ -z "${AUTHELIA_CONTAINER_UID}" || -z "${AUTHELIA_CONTAINER_GID}" ]]; then
  AUTHELIA_CONTAINER_UID="$(stat -c '%u' "${AUTHELIA_DIR}" 2>/dev/null || id -u)"
  AUTHELIA_CONTAINER_GID="$(stat -c '%g' "${AUTHELIA_DIR}" 2>/dev/null || id -g)"
  set_env AUTHELIA_CONTAINER_UID "${AUTHELIA_CONTAINER_UID}"
  set_env AUTHELIA_CONTAINER_GID "${AUTHELIA_CONTAINER_GID}"
fi

PUBLIC_DOMAIN="$(get_env PUBLIC_DOMAIN)"
if [[ -z "${PUBLIC_DOMAIN}" ]]; then
  echo "PUBLIC_DOMAIN is required in .env before bootstrapping Authelia." >&2
  exit 1
fi

AUTHELIA_USERS_SOURCE="$(get_env AUTHELIA_USERS_SOURCE)"
if [[ -z "${AUTHELIA_USERS_SOURCE}" ]]; then
  AUTHELIA_USERS_SOURCE="file"
  set_env AUTHELIA_USERS_SOURCE "${AUTHELIA_USERS_SOURCE}"
fi

if [[ "${AUTHELIA_USERS_SOURCE}" != "file" && "${AUTHELIA_USERS_SOURCE}" != "env" ]]; then
  echo "AUTHELIA_USERS_SOURCE must be 'file' or 'env'" >&2
  exit 1
fi

AUTHELIA_AUTH_USERNAME="$(resolve_secret_with_vault AUTHELIA_AUTH_USERNAME kv/study-agents/authelia-auth-username default_gateway_admin)"
AUTHELIA_AUTH_PASSWORD="$(resolve_secret_with_vault AUTHELIA_AUTH_PASSWORD kv/study-agents/authelia-auth-password)"

AUTHELIA_SESSION_SECRET="$(resolve_secret_with_vault AUTHELIA_SESSION_SECRET kv/study-agents/authelia-session-secret rand_hex_32)"
AUTHELIA_STORAGE_ENCRYPTION_KEY="$(resolve_secret_with_vault AUTHELIA_STORAGE_ENCRYPTION_KEY kv/study-agents/authelia-storage-encryption-key rand_hex_32)"
AUTHELIA_JWT_SECRET="$(resolve_secret_with_vault AUTHELIA_JWT_SECRET kv/study-agents/authelia-jwt-secret rand_hex_32)"

AUTHELIA_POLICY="$(get_env AUTHELIA_POLICY)"
if [[ -z "${AUTHELIA_POLICY}" ]]; then
  AUTHELIA_POLICY="two_factor"
  set_env AUTHELIA_POLICY "${AUTHELIA_POLICY}"
fi

AUTHELIA_DEFAULT_2FA_METHOD="$(get_env AUTHELIA_DEFAULT_2FA_METHOD)"
if [[ -z "${AUTHELIA_DEFAULT_2FA_METHOD}" ]]; then
  AUTHELIA_DEFAULT_2FA_METHOD="totp"
  set_env AUTHELIA_DEFAULT_2FA_METHOD "${AUTHELIA_DEFAULT_2FA_METHOD}"
fi

AUTHELIA_SESSION_INACTIVITY="$(get_env AUTHELIA_SESSION_INACTIVITY)"
if [[ -z "${AUTHELIA_SESSION_INACTIVITY}" ]]; then
  AUTHELIA_SESSION_INACTIVITY="30 minutes"
  set_env AUTHELIA_SESSION_INACTIVITY "${AUTHELIA_SESSION_INACTIVITY}"
fi

AUTHELIA_SESSION_EXPIRATION="$(get_env AUTHELIA_SESSION_EXPIRATION)"
if [[ -z "${AUTHELIA_SESSION_EXPIRATION}" ]]; then
  AUTHELIA_SESSION_EXPIRATION="3 hours"
  set_env AUTHELIA_SESSION_EXPIRATION "${AUTHELIA_SESSION_EXPIRATION}"
fi

AUTHELIA_SESSION_REMEMBER_ME="$(get_env AUTHELIA_SESSION_REMEMBER_ME)"
if [[ -z "${AUTHELIA_SESSION_REMEMBER_ME}" ]]; then
  AUTHELIA_SESSION_REMEMBER_ME="1 week"
  set_env AUTHELIA_SESSION_REMEMBER_ME "${AUTHELIA_SESSION_REMEMBER_ME}"
fi

AUTHELIA_OIDC_HMAC_SECRET="$(resolve_secret_with_vault AUTHELIA_OIDC_HMAC_SECRET kv/study-agents/authelia-oidc-hmac-secret rand_hex_32)"

AUTHELIA_OIDC_CLIENT_ID="$(get_env AUTHELIA_OIDC_CLIENT_ID)"
if [[ -z "${AUTHELIA_OIDC_CLIENT_ID}" ]]; then
  AUTHELIA_OIDC_CLIENT_ID="study-agents"
  set_env AUTHELIA_OIDC_CLIENT_ID "${AUTHELIA_OIDC_CLIENT_ID}"
fi

AUTHELIA_OIDC_CLIENT_SECRET="$(resolve_secret_with_vault AUTHELIA_OIDC_CLIENT_SECRET kv/study-agents/authelia-oidc-client-secret rand_password)"

AUTHELIA_OIDC_CLIENT_REDIRECT_URI="$(get_env AUTHELIA_OIDC_CLIENT_REDIRECT_URI)"
if [[ -z "${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}" ]]; then
  AUTHELIA_OIDC_CLIENT_REDIRECT_URI="https://${PUBLIC_DOMAIN}/oidc/callback"
  set_env AUTHELIA_OIDC_CLIENT_REDIRECT_URI "${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}"
fi

AUTHELIA_VAULT_OIDC_CLIENT_ID="$(get_env AUTHELIA_VAULT_OIDC_CLIENT_ID)"
if [[ -z "${AUTHELIA_VAULT_OIDC_CLIENT_ID}" ]]; then
  AUTHELIA_VAULT_OIDC_CLIENT_ID="vault"
  set_env AUTHELIA_VAULT_OIDC_CLIENT_ID "${AUTHELIA_VAULT_OIDC_CLIENT_ID}"
fi

AUTHELIA_VAULT_OIDC_CLIENT_SECRET="$(resolve_secret_with_vault AUTHELIA_VAULT_OIDC_CLIENT_SECRET kv/study-agents/authelia-vault-oidc-client-secret rand_password)"

AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI="$(get_env AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI)"
if [[ -z "${AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI}" ]]; then
  AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI="https://${PUBLIC_DOMAIN}/ui/vault/auth/oidc/oidc/callback"
  set_env AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI "${AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI}"
fi

GATEWAY_ALLOWED_CIDRS="$(get_env GATEWAY_ALLOWED_CIDRS)"
if [[ -z "${GATEWAY_ALLOWED_CIDRS}" ]]; then
  SSH_ADMIN_IP="$(echo "${SSH_CONNECTION:-}" | awk '{print $1}')"
  if [[ -n "${SSH_ADMIN_IP}" ]]; then
    GATEWAY_ALLOWED_CIDRS="127.0.0.1/32 ::1/128 ${SSH_ADMIN_IP}/32"
  else
    GATEWAY_ALLOWED_CIDRS="127.0.0.1/32 ::1/128"
  fi
  set_env GATEWAY_ALLOWED_CIDRS "${GATEWAY_ALLOWED_CIDRS}"
fi

if [[ "${AUTHELIA_USERS_SOURCE}" == "env" ]]; then
  if [[ -z "${AUTHELIA_AUTH_PASSWORD}" ]]; then
    AUTHELIA_AUTH_PASSWORD="$(rand_password)"
    vault_kv_put_value kv/study-agents/authelia-auth-password "${AUTHELIA_AUTH_PASSWORD}" || true
    set_env_secret_if_allowed AUTHELIA_AUTH_PASSWORD "${AUTHELIA_AUTH_PASSWORD}"
  fi

  AUTHELIA_AUTH_PASSWORD_HASH="$(
    docker run --rm authelia/authelia:latest \
      authelia crypto hash generate argon2 --password "${AUTHELIA_AUTH_PASSWORD}" --no-confirm \
      | awk -F'Digest: ' '/Digest: / {print $2; exit}'
  )"

  if [[ -z "${AUTHELIA_AUTH_PASSWORD_HASH}" ]]; then
    echo "Failed to generate Authelia password hash." >&2
    exit 1
  fi

  cat > "${AUTHELIA_USERS}" <<EOF_USERS
users:
  ${AUTHELIA_AUTH_USERNAME}:
    disabled: false
    displayname: Gateway Administrator
    password: ${AUTHELIA_AUTH_PASSWORD_HASH}
    email: admin@${PUBLIC_DOMAIN}
    groups:
      - admins
EOF_USERS
elif ! users_file_has_entries; then
  if [[ -z "${AUTHELIA_AUTH_PASSWORD}" ]]; then
    AUTHELIA_AUTH_PASSWORD="$(rand_password)"
    vault_kv_put_value kv/study-agents/authelia-auth-password "${AUTHELIA_AUTH_PASSWORD}" || true
    set_env_secret_if_allowed AUTHELIA_AUTH_PASSWORD "${AUTHELIA_AUTH_PASSWORD}"
  fi

  AUTHELIA_AUTH_PASSWORD_HASH="$(
    docker run --rm authelia/authelia:latest \
      authelia crypto hash generate argon2 --password "${AUTHELIA_AUTH_PASSWORD}" --no-confirm \
      | awk -F'Digest: ' '/Digest: / {print $2; exit}'
  )"

  if [[ -z "${AUTHELIA_AUTH_PASSWORD_HASH}" ]]; then
    echo "Failed to generate initial Authelia password hash." >&2
    exit 1
  fi

  cat > "${AUTHELIA_USERS}" <<EOF_USERS
users:
  ${AUTHELIA_AUTH_USERNAME}:
    disabled: false
    displayname: Gateway Administrator
    password: ${AUTHELIA_AUTH_PASSWORD_HASH}
    email: admin@${PUBLIC_DOMAIN}
    groups:
      - admins
EOF_USERS
fi

AUTHELIA_OIDC_CLIENT_SECRET_HASH="$(
  docker run --rm authelia/authelia:latest \
    authelia crypto hash generate pbkdf2 --password "${AUTHELIA_OIDC_CLIENT_SECRET}" --no-confirm \
    | awk -F'Digest: ' '/Digest: / {print $2; exit}'
)"

if [[ -z "${AUTHELIA_OIDC_CLIENT_SECRET_HASH}" ]]; then
  echo "Failed to generate Authelia OIDC client secret hash." >&2
  exit 1
fi

AUTHELIA_VAULT_OIDC_CLIENT_SECRET_HASH="$(
  docker run --rm authelia/authelia:latest \
    authelia crypto hash generate pbkdf2 --password "${AUTHELIA_VAULT_OIDC_CLIENT_SECRET}" --no-confirm \
    | awk -F'Digest: ' '/Digest: / {print $2; exit}'
)"

if [[ -z "${AUTHELIA_VAULT_OIDC_CLIENT_SECRET_HASH}" ]]; then
  echo "Failed to generate Vault OIDC client secret hash." >&2
  exit 1
fi

if [[ ! -s "${AUTHELIA_OIDC_JWKS_KEY_PATH}" ]]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${AUTHELIA_OIDC_JWKS_KEY_PATH}"
fi

OIDC_JWKS_KEY_BLOCK="$(
  sed 's/^/          /' "${AUTHELIA_OIDC_JWKS_KEY_PATH}"
)"

cat > "${AUTHELIA_CONFIG}" <<EOF
theme: auto
certificates_directory: /tls

server:
  address: 'tcp://:9091/authelia'
  tls:
    key: /tls/authelia.key
    certificate: /tls/authelia.crt
  endpoints:
    authz:
      forward-auth:
        implementation: 'ForwardAuth'

log:
  level: info

default_2fa_method: ${AUTHELIA_DEFAULT_2FA_METHOD}

totp:
  issuer: ${PUBLIC_DOMAIN}

authentication_backend:
  file:
    path: /config/users_database.yml
    watch: false
    password:
      algorithm: argon2

access_control:
  default_policy: deny
  rules:
    - domain: ${PUBLIC_DOMAIN}
      resources:
        - '^/authelia(/.*)?$'
      policy: bypass
    - domain: ${PUBLIC_DOMAIN}
      policy: ${AUTHELIA_POLICY}

identity_providers:
  oidc:
    hmac_secret: ${AUTHELIA_OIDC_HMAC_SECRET}
    jwks:
      - key_id: "main-rs256"
        algorithm: "RS256"
        use: "sig"
        key: |
${OIDC_JWKS_KEY_BLOCK}
    clients:
      - client_id: "${AUTHELIA_OIDC_CLIENT_ID}"
        client_name: "Study Agents"
        client_secret: '${AUTHELIA_OIDC_CLIENT_SECRET_HASH}'
        public: false
        authorization_policy: "${AUTHELIA_POLICY}"
        redirect_uris:
          - "${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}"
        scopes:
          - "openid"
          - "profile"
          - "email"
          - "groups"
        grant_types:
          - "authorization_code"
        response_types:
          - "code"
        token_endpoint_auth_method: "client_secret_basic"
      - client_id: "${AUTHELIA_VAULT_OIDC_CLIENT_ID}"
        client_name: "Vault"
        client_secret: '${AUTHELIA_VAULT_OIDC_CLIENT_SECRET_HASH}'
        public: false
        authorization_policy: "${AUTHELIA_POLICY}"
        redirect_uris:
          - "${AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI}"
          - "http://localhost:8250/oidc/callback"
        scopes:
          - "openid"
          - "profile"
          - "email"
          - "groups"
        grant_types:
          - "authorization_code"
        response_types:
          - "code"
        token_endpoint_auth_method: "client_secret_basic"

session:
  secret: ${AUTHELIA_SESSION_SECRET}
  inactivity: "${AUTHELIA_SESSION_INACTIVITY}"
  expiration: "${AUTHELIA_SESSION_EXPIRATION}"
  remember_me: "${AUTHELIA_SESSION_REMEMBER_ME}"
  cookies:
    - name: authelia_session
      domain: ${PUBLIC_DOMAIN}
      authelia_url: https://${PUBLIC_DOMAIN}/authelia
      default_redirection_url: https://${PUBLIC_DOMAIN}/
      same_site: lax
  redis:
    host: redis
    port: 6379

regulation:
  max_retries: 3
  find_time: 20 minutes
  ban_time: 20 minutes
  modes:
    - ip

storage:
  encryption_key: ${AUTHELIA_STORAGE_ENCRYPTION_KEY}
  local:
    path: /config/runtime/db.sqlite3

identity_validation:
  reset_password:
    jwt_secret: ${AUTHELIA_JWT_SECRET}

notifier:
  filesystem:
    filename: /config/runtime/notification.txt
EOF

chmod 600 "${AUTHELIA_CONFIG}" "${AUTHELIA_OIDC_JWKS_KEY_PATH}" || true
if [[ -f "${AUTHELIA_USERS}" ]]; then
  chmod 600 "${AUTHELIA_USERS}" || true
fi
chmod 700 "${AUTHELIA_RUNTIME_DIR}" || true
ensure_path_owner "${AUTHELIA_CONTAINER_UID}:${AUTHELIA_CONTAINER_GID}" "${AUTHELIA_RUNTIME_DIR}"
ensure_path_owner "${AUTHELIA_CONTAINER_UID}:${AUTHELIA_CONTAINER_GID}" "${AUTHELIA_CONFIG}"
ensure_path_owner "${AUTHELIA_CONTAINER_UID}:${AUTHELIA_CONTAINER_GID}" "${AUTHELIA_OIDC_JWKS_KEY_PATH}"
if [[ -f "${AUTHELIA_USERS}" ]]; then
  ensure_path_owner "${AUTHELIA_CONTAINER_UID}:${AUTHELIA_CONTAINER_GID}" "${AUTHELIA_USERS}"
fi

echo "Authelia bootstrap complete."
echo "  users source: ${AUTHELIA_USERS_SOURCE}"
echo "  username: ${AUTHELIA_AUTH_USERNAME}"
if [[ "${AUTHELIA_USERS_SOURCE}" == "env" || ! users_file_has_entries ]]; then
  echo "  password: ${AUTHELIA_AUTH_PASSWORD}"
else
  echo "  password: (managed via users_database.yml / authelia_user_manage.sh)"
fi
echo "  policy: ${AUTHELIA_POLICY}"
echo "  default 2FA method: ${AUTHELIA_DEFAULT_2FA_METHOD}"
echo "  OIDC client id: ${AUTHELIA_OIDC_CLIENT_ID}"
echo "  OIDC client redirect URI: ${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}"
echo "  Vault OIDC client id: ${AUTHELIA_VAULT_OIDC_CLIENT_ID}"
echo "  Vault OIDC redirect URI: ${AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI}"
echo "  allowed CIDRs: ${GATEWAY_ALLOWED_CIDRS}"
