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
  PATH_ARG="${path}" KEY_ARG="${key}" python - <<'PY' 2>/dev/null || true
import json
import os
import ssl
import urllib.request

addr = os.environ.get("VAULT_ADDR", "").strip().rstrip("/")
token = os.environ.get("VAULT_TOKEN", "").strip()
path = os.environ.get("PATH_ARG", "").strip()
key = os.environ.get("KEY_ARG", "").strip()

if not addr or not token or not path or not key:
    raise SystemExit(0)

url = f"{addr}/v1/{path}"
context = None

if url.startswith("https://"):
    skip_verify = os.environ.get("VAULT_SKIP_VERIFY", "").strip().lower() in {"1", "true", "yes"}
    if skip_verify:
        context = ssl._create_unverified_context()
    else:
        cacert = os.environ.get("VAULT_CACERT", "").strip() or None
        capath = os.environ.get("VAULT_CAPATH", "").strip() or None
        context = ssl.create_default_context(cafile=cacert, capath=capath)

request = urllib.request.Request(url, headers={"X-Vault-Token": token})

try:
    with urllib.request.urlopen(request, context=context, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(0)

print(payload.get("data", {}).get("data", {}).get(key, ""), end="")
PY
}

# Preserve existing env if already set.
OPENAI_API_KEY=${OPENAI_API_KEY:-$(fetch_secret "kv/data/study-agents/openai" "value")}
SUPABASE_URL=${SUPABASE_URL:-$(fetch_secret "kv/data/study-agents/supabase-url" "value")}
SUPABASE_KEY=${SUPABASE_KEY:-$(fetch_secret "kv/data/study-agents/supabase-key" "value")}
API_TOKEN=${API_TOKEN:-$(fetch_secret "kv/data/study-agents/api-token" "value")}
COPILOT_API_KEY=${COPILOT_API_KEY:-$(fetch_secret "kv/data/study-agents/copilot-api-key" "value")}
RAG_API_TOKEN=${RAG_API_TOKEN:-$(fetch_secret "kv/data/study-agents/rag-api-token" "value")}
SCENARIO_API_KEY=${SCENARIO_API_KEY:-$(fetch_secret "kv/data/study-agents/scenario-api-key" "value")}

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
