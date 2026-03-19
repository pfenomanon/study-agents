#!/usr/bin/env bash
set -euo pipefail

# This script fetches secrets from Vault (if VAULT_ADDR/VAULT_TOKEN are set) and
# exports them before executing the given command. Falls back to existing env.

ENV_OUT="/env/.env.runtime"
mkdir -p /env

fetch_secret() {
  local path="$1"
  local key="$2"
  if [ -z "${VAULT_TOKEN:-}" ] || [ -z "${VAULT_ADDR:-}" ]; then
    return 0
  fi
  resp="$(curl -sSf -H "X-Vault-Token: ${VAULT_TOKEN}" "${VAULT_ADDR}/v1/${path}" || true)"
  if [ -z "$resp" ]; then
    return 0
  fi
  python - <<'PY' 2>/dev/null || true
import json, os, sys
data = json.loads(os.environ["RESP"])
print(data.get("data", {}).get("data", {}).get(os.environ["KEY"], ""))
PY
}

# Preserve existing env if already set.
OPENAI_API_KEY=${OPENAI_API_KEY:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/openai" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
SUPABASE_URL=${SUPABASE_URL:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/supabase-url" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
SUPABASE_KEY=${SUPABASE_KEY:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/supabase-key" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
API_TOKEN=${API_TOKEN:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/api-token" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
COPILOT_API_KEY=${COPILOT_API_KEY:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/copilot-api-key" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
RAG_API_TOKEN=${RAG_API_TOKEN:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/rag-api-token" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}
SCENARIO_API_KEY=${SCENARIO_API_KEY:-$(RESP="$(VAULT_TOKEN=${VAULT_TOKEN:-} VAULT_ADDR=${VAULT_ADDR:-} fetch_secret "kv/data/study-agents/scenario-api-key" "value")" KEY=value python - <<'PY' 2>/dev/null || true
import os
print(os.environ.get("RESP",""))
PY
)}

cat >"$ENV_OUT" <<EOF
OPENAI_API_KEY=${OPENAI_API_KEY}
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_KEY=${SUPABASE_KEY}
API_TOKEN=${API_TOKEN}
COPILOT_API_KEY=${COPILOT_API_KEY}
RAG_API_TOKEN=${RAG_API_TOKEN}
SCENARIO_API_KEY=${SCENARIO_API_KEY}
EOF

set -a
# shellcheck disable=SC1090
[ -f "$ENV_OUT" ] && . "$ENV_OUT"
set +a

exec "$@"
