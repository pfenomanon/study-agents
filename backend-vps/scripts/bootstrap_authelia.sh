#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
AUTHELIA_DIR="${ROOT_DIR}/docker/authelia"
AUTHELIA_CONFIG="${AUTHELIA_DIR}/configuration.yml"
AUTHELIA_USERS="${AUTHELIA_DIR}/users_database.yml"
AUTHELIA_OIDC_JWKS_KEY_PATH="${AUTHELIA_DIR}/oidc_jwks_rs256.pem"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo ".env file not found at ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${AUTHELIA_DIR}"

get_env() {
  local key="$1"
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' "${ENV_FILE}"
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

rand_hex() {
  local bytes="$1"
  openssl rand -hex "${bytes}"
}

rand_password() {
  openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-24
}

PUBLIC_DOMAIN="$(get_env PUBLIC_DOMAIN)"
if [[ -z "${PUBLIC_DOMAIN}" ]]; then
  echo "PUBLIC_DOMAIN is required in .env before bootstrapping Authelia." >&2
  exit 1
fi

AUTHELIA_AUTH_USERNAME="$(get_env AUTHELIA_AUTH_USERNAME)"
if [[ -z "${AUTHELIA_AUTH_USERNAME}" ]]; then
  AUTHELIA_AUTH_USERNAME="gateway-admin"
  set_env AUTHELIA_AUTH_USERNAME "${AUTHELIA_AUTH_USERNAME}"
fi

AUTHELIA_AUTH_PASSWORD="$(get_env AUTHELIA_AUTH_PASSWORD)"
if [[ -z "${AUTHELIA_AUTH_PASSWORD}" ]]; then
  AUTHELIA_AUTH_PASSWORD="$(rand_password)"
  set_env AUTHELIA_AUTH_PASSWORD "${AUTHELIA_AUTH_PASSWORD}"
fi

AUTHELIA_SESSION_SECRET="$(get_env AUTHELIA_SESSION_SECRET)"
if [[ -z "${AUTHELIA_SESSION_SECRET}" ]]; then
  AUTHELIA_SESSION_SECRET="$(rand_hex 32)"
  set_env AUTHELIA_SESSION_SECRET "${AUTHELIA_SESSION_SECRET}"
fi

AUTHELIA_STORAGE_ENCRYPTION_KEY="$(get_env AUTHELIA_STORAGE_ENCRYPTION_KEY)"
if [[ -z "${AUTHELIA_STORAGE_ENCRYPTION_KEY}" ]]; then
  AUTHELIA_STORAGE_ENCRYPTION_KEY="$(rand_hex 32)"
  set_env AUTHELIA_STORAGE_ENCRYPTION_KEY "${AUTHELIA_STORAGE_ENCRYPTION_KEY}"
fi

AUTHELIA_JWT_SECRET="$(get_env AUTHELIA_JWT_SECRET)"
if [[ -z "${AUTHELIA_JWT_SECRET}" ]]; then
  AUTHELIA_JWT_SECRET="$(rand_hex 32)"
  set_env AUTHELIA_JWT_SECRET "${AUTHELIA_JWT_SECRET}"
fi

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

AUTHELIA_OIDC_HMAC_SECRET="$(get_env AUTHELIA_OIDC_HMAC_SECRET)"
if [[ -z "${AUTHELIA_OIDC_HMAC_SECRET}" ]]; then
  AUTHELIA_OIDC_HMAC_SECRET="$(rand_hex 32)"
  set_env AUTHELIA_OIDC_HMAC_SECRET "${AUTHELIA_OIDC_HMAC_SECRET}"
fi

AUTHELIA_OIDC_CLIENT_ID="$(get_env AUTHELIA_OIDC_CLIENT_ID)"
if [[ -z "${AUTHELIA_OIDC_CLIENT_ID}" ]]; then
  AUTHELIA_OIDC_CLIENT_ID="study-agents"
  set_env AUTHELIA_OIDC_CLIENT_ID "${AUTHELIA_OIDC_CLIENT_ID}"
fi

AUTHELIA_OIDC_CLIENT_SECRET="$(get_env AUTHELIA_OIDC_CLIENT_SECRET)"
if [[ -z "${AUTHELIA_OIDC_CLIENT_SECRET}" ]]; then
  AUTHELIA_OIDC_CLIENT_SECRET="$(rand_password)"
  set_env AUTHELIA_OIDC_CLIENT_SECRET "${AUTHELIA_OIDC_CLIENT_SECRET}"
fi

AUTHELIA_OIDC_CLIENT_REDIRECT_URI="$(get_env AUTHELIA_OIDC_CLIENT_REDIRECT_URI)"
if [[ -z "${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}" ]]; then
  AUTHELIA_OIDC_CLIENT_REDIRECT_URI="https://${PUBLIC_DOMAIN}/oidc/callback"
  set_env AUTHELIA_OIDC_CLIENT_REDIRECT_URI "${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}"
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

AUTHELIA_AUTH_PASSWORD_HASH="$(
  docker run --rm authelia/authelia:latest \
    authelia crypto hash generate argon2 --password "${AUTHELIA_AUTH_PASSWORD}" --no-confirm \
    | awk -F'Digest: ' '/Digest: / {print $2; exit}'
)"

if [[ -z "${AUTHELIA_AUTH_PASSWORD_HASH}" ]]; then
  echo "Failed to generate Authelia password hash." >&2
  exit 1
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

if [[ ! -s "${AUTHELIA_OIDC_JWKS_KEY_PATH}" ]]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "${AUTHELIA_OIDC_JWKS_KEY_PATH}"
fi

OIDC_JWKS_KEY_BLOCK="$(
  sed 's/^/          /' "${AUTHELIA_OIDC_JWKS_KEY_PATH}"
)"

cat > "${AUTHELIA_USERS}" <<EOF
users:
  ${AUTHELIA_AUTH_USERNAME}:
    disabled: false
    displayname: Gateway Administrator
    password: ${AUTHELIA_AUTH_PASSWORD_HASH}
    email: admin@${PUBLIC_DOMAIN}
    groups:
      - admins
EOF

cat > "${AUTHELIA_CONFIG}" <<EOF
theme: auto

server:
  address: 'tcp://:9091/authelia'
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
    path: /config/db.sqlite3

identity_validation:
  reset_password:
    jwt_secret: ${AUTHELIA_JWT_SECRET}

notifier:
  filesystem:
    filename: /config/notification.txt
EOF

chmod 600 "${AUTHELIA_USERS}" "${AUTHELIA_CONFIG}"
chmod 600 "${AUTHELIA_OIDC_JWKS_KEY_PATH}"

echo "Authelia bootstrap complete."
echo "  username: ${AUTHELIA_AUTH_USERNAME}"
echo "  password: ${AUTHELIA_AUTH_PASSWORD}"
echo "  policy: ${AUTHELIA_POLICY}"
echo "  default 2FA method: ${AUTHELIA_DEFAULT_2FA_METHOD}"
echo "  session inactivity: ${AUTHELIA_SESSION_INACTIVITY}"
echo "  session expiration: ${AUTHELIA_SESSION_EXPIRATION}"
echo "  session remember_me: ${AUTHELIA_SESSION_REMEMBER_ME}"
echo "  OIDC client id: ${AUTHELIA_OIDC_CLIENT_ID}"
echo "  OIDC client redirect URI: ${AUTHELIA_OIDC_CLIENT_REDIRECT_URI}"
echo "  OIDC client secret: ${AUTHELIA_OIDC_CLIENT_SECRET}"
echo "  allowed CIDRs: ${GATEWAY_ALLOWED_CIDRS}"
