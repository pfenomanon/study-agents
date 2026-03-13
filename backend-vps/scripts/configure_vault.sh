#!/usr/bin/env bash
set -euo pipefail

# Configure Vault secrets for study-agents and (optionally) bring up the stack.
# This script will:
#   1) docker compose up -d --build
#   2) Wait for Vault to respond
#   3) Prompt (or read env vars) for secrets and write them to Vault KV
#   4) Optionally write VAULT_ADDR / VAULT_TOKEN to .env so services can pull secrets via use_env.sh
#
# NOTE: Storing tokens in .env is sensitive. Use only on trusted hosts; prefer a non-dev token.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
VAULT_ADDR_DEFAULT="${VAULT_ADDR:-http://localhost:8200}"
VAULT_TOKEN_DEFAULT="${VAULT_TOKEN:-root}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }
}

require_cmd docker
require_cmd vault
require_cmd curl

echo "==> Starting stack (docker compose up -d --build)..."
docker compose -f "${ROOT}/docker-compose.yml" up -d --build

VAULT_ADDR="${VAULT_ADDR_DEFAULT}"
VAULT_TOKEN="${VAULT_TOKEN_DEFAULT}"

read -rp "Vault address [${VAULT_ADDR}]: " addr_input
VAULT_ADDR="${addr_input:-$VAULT_ADDR}"
read -rp "Vault token [${VAULT_TOKEN}]: " token_input
VAULT_TOKEN="${token_input:-$VAULT_TOKEN}"

echo "==> Waiting for Vault to respond at ${VAULT_ADDR}..."
for i in $(seq 1 30); do
  if curl -sSf "${VAULT_ADDR}/v1/sys/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo "Vault not responding at ${VAULT_ADDR}" >&2
    exit 1
  fi
done

prompt_secret() {
  local name="$1" var="$2"
  local current="${!var:-}"
  if [ -n "$current" ]; then
    echo "$name found in env."
    return
  fi
  read -rs -p "Enter ${name}: " value
  echo
  export "$var"="$value"
}

prompt_secret "OPENAI_API_KEY" OPENAI_API_KEY
prompt_secret "SUPABASE_URL" SUPABASE_URL
prompt_secret "SUPABASE_KEY" SUPABASE_KEY
prompt_secret "API_TOKEN (for CAG API)" API_TOKEN
prompt_secret "COPILOT_API_KEY (for Copilot endpoints)" COPILOT_API_KEY

echo "==> Writing secrets to Vault KV (kv/study-agents/*)..."
export VAULT_ADDR VAULT_TOKEN
vault kv put kv/study-agents/openai value="${OPENAI_API_KEY}"
vault kv put kv/study-agents/supabase-url value="${SUPABASE_URL}"
vault kv put kv/study-agents/supabase-key value="${SUPABASE_KEY}"
vault kv put kv/study-agents/api-token value="${API_TOKEN}"
vault kv put kv/study-agents/copilot-api-key value="${COPILOT_API_KEY}"

echo "==> Updating .env with VAULT_ADDR / VAULT_TOKEN (sensitive; for local use only)..."
touch "${ENV_FILE}"
cp "${ENV_FILE}" "${ENV_FILE}.bak.$(date +%s)"
grep -vE '^(VAULT_ADDR|VAULT_TOKEN)=' "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
{
  echo "VAULT_ADDR=${VAULT_ADDR}"
  echo "VAULT_TOKEN=${VAULT_TOKEN}"
} >> "${ENV_FILE}.tmp"
mv "${ENV_FILE}.tmp" "${ENV_FILE}"

echo "==> Restarting services to pick up Vault-based env..."
docker compose -f "${ROOT}/docker-compose.yml" up -d

cat <<'EOF'
All done.
- Secrets stored under kv/study-agents/* in Vault.
- VAULT_ADDR / VAULT_TOKEN written to .env (backup created). Remove/rotate as needed.
- Services restarted; use_env.sh will pull secrets at startup.
Security note: Do not keep real tokens/keys in .env for shared/prod environments.
EOF
