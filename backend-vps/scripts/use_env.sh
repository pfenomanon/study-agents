#!/usr/bin/env bash
set -euo pipefail

ENV_DIR="/env"
ENV_OUT="${ENV_DIR}/.env.runtime"
VAULT_TOKEN_CACHE="${ENV_DIR}/.vault_token.json"

mkdir -p "${ENV_DIR}"

runtime_get() {
  local key="$1"
  [[ -f "${ENV_OUT}" ]] || return 0
  awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, $2)); exit}' "${ENV_OUT}" 2>/dev/null || true
}

json_get() {
  local json="$1"
  local path="$2"
  JSON_PAYLOAD="${json}" JSON_PATH="${path}" python - <<'PY' 2>/dev/null || true
import json
import os

raw = os.environ.get("JSON_PAYLOAD", "").strip()
path = [p for p in os.environ.get("JSON_PATH", "").split(".") if p]
if not raw or not path:
    raise SystemExit(0)

obj = json.loads(raw)
for segment in path:
    if isinstance(obj, dict) and segment in obj:
        obj = obj[segment]
    else:
        raise SystemExit(0)

if obj is None:
    raise SystemExit(0)
print(obj)
PY
}

vault_http() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local tmp_body tmp_code code status

  [[ -n "${VAULT_ADDR:-}" ]] || return 1

  tmp_body="$(mktemp)"
  tmp_code="$(mktemp)"
  trap 'rm -f "${tmp_body}" "${tmp_code}"' RETURN

  status=0
  VAULT_HTTP_METHOD="${method}" \
  VAULT_HTTP_PATH="${path}" \
  VAULT_HTTP_BODY="${body}" \
  VAULT_HTTP_ADDR="${VAULT_ADDR}" \
  VAULT_HTTP_CACERT="${VAULT_CACERT:-}" \
  VAULT_HTTP_TOKEN="${VAULT_TOKEN:-}" \
  VAULT_HTTP_TIMEOUT="${VAULT_REQUEST_TIMEOUT:-10}" \
  VAULT_HTTP_CODE_FILE="${tmp_code}" \
  VAULT_HTTP_BODY_FILE="${tmp_body}" \
  python3 - <<'PY' || status=$?
import os
import ssl
import urllib.error
import urllib.request

method = os.environ.get("VAULT_HTTP_METHOD", "GET")
path = os.environ.get("VAULT_HTTP_PATH", "").lstrip("/")
body = os.environ.get("VAULT_HTTP_BODY", "")
addr = os.environ.get("VAULT_HTTP_ADDR", "").rstrip("/")
cacert = os.environ.get("VAULT_HTTP_CACERT", "").strip()
token = os.environ.get("VAULT_HTTP_TOKEN", "").strip()
timeout_raw = os.environ.get("VAULT_HTTP_TIMEOUT", "10").strip()
code_file = os.environ["VAULT_HTTP_CODE_FILE"]
body_file = os.environ["VAULT_HTTP_BODY_FILE"]

if not addr or not path:
    raise SystemExit(1)

try:
    timeout = float(timeout_raw)
except ValueError:
    timeout = 10.0
if timeout <= 0:
    timeout = 10.0

url = f"{addr}/v1/{path}"
data = body.encode("utf-8") if body else None
request = urllib.request.Request(url=url, data=data, method=method)
if token:
    request.add_header("X-Vault-Token", token)
if body:
    request.add_header("Content-Type", "application/json")

if cacert and os.path.isfile(cacert):
    context = ssl.create_default_context(cafile=cacert)
else:
    context = ssl.create_default_context()

status_code = None
response_body = b""
exit_code = 1
try:
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        status_code = response.status
        response_body = response.read()
        exit_code = 0 if 200 <= status_code < 300 else 2
except urllib.error.HTTPError as exc:
    status_code = exc.code
    response_body = exc.read() or b""
    exit_code = 2
except Exception:
    exit_code = 1

if status_code is not None:
    with open(code_file, "w", encoding="utf-8") as fh:
        fh.write(str(status_code))
with open(body_file, "wb") as fh:
    fh.write(response_body)

raise SystemExit(exit_code)
PY

  if [[ "${status}" -ne 0 && "${status}" -ne 2 ]]; then
    return 1
  fi

  code="$(cat "${tmp_code}")"
  if [[ "${code}" -lt 200 || "${code}" -ge 300 ]]; then
    return 1
  fi

  cat "${tmp_body}"
}

load_cached_vault_token() {
  local raw token expires now
  [[ -z "${VAULT_TOKEN:-}" ]] || return 0
  [[ -f "${VAULT_TOKEN_CACHE}" ]] || return 1

  raw="$(cat "${VAULT_TOKEN_CACHE}" 2>/dev/null || true)"
  token="$(json_get "${raw}" token)"
  expires="$(json_get "${raw}" expires_at_epoch)"
  now="$(date +%s)"

  [[ -n "${token}" ]] || return 1
  [[ -n "${expires}" ]] || return 1
  [[ "${expires}" =~ ^[0-9]+$ ]] || return 1
  (( expires > now + 30 )) || return 1

  export VAULT_TOKEN="${token}"
  return 0
}

vault_login_approle() {
  local auth_method role_id secret_id payload response token lease_duration now expires

  auth_method="${VAULT_AUTH_METHOD:-token}"
  [[ "${auth_method}" == "approle" ]] || return 0

  if [[ -n "${VAULT_TOKEN:-}" ]]; then
    return 0
  fi

  if load_cached_vault_token; then
    return 0
  fi

  role_id="${VAULT_ROLE_ID:-}"
  if [[ -z "${role_id}" && -n "${VAULT_ROLE_ID_FILE:-}" && -r "${VAULT_ROLE_ID_FILE}" ]]; then
    role_id="$(head -n1 "${VAULT_ROLE_ID_FILE}" | tr -d '\r\n')"
  fi

  secret_id="${VAULT_SECRET_ID:-}"
  if [[ -z "${secret_id}" && -n "${VAULT_SECRET_ID_FILE:-}" && -r "${VAULT_SECRET_ID_FILE}" ]]; then
    secret_id="$(head -n1 "${VAULT_SECRET_ID_FILE}" | tr -d '\r\n')"
  fi

  [[ -n "${role_id}" && -n "${secret_id}" ]] || return 1

  payload="$(ROLE_ID="${role_id}" SECRET_ID="${secret_id}" python - <<'PY'
import json
import os
print(json.dumps({"role_id": os.environ["ROLE_ID"], "secret_id": os.environ["SECRET_ID"]}))
PY
)"

  response="$(vault_http POST auth/approle/login "${payload}" || true)"
  token="$(json_get "${response}" auth.client_token)"
  lease_duration="$(json_get "${response}" auth.lease_duration)"

  [[ -n "${token}" ]] || return 1
  if [[ ! "${lease_duration}" =~ ^[0-9]+$ ]]; then
    lease_duration=3600
  fi

  export VAULT_TOKEN="${token}"

  now="$(date +%s)"
  expires="$((now + lease_duration - 30))"
  umask 077
  cat > "${VAULT_TOKEN_CACHE}" <<EOF_TOKEN
{"token":"${token}","expires_at_epoch":${expires}}
EOF_TOKEN
  chmod 600 "${VAULT_TOKEN_CACHE}" || true

  return 0
}

fetch_secret_value() {
  local path="$1"
  local response value

  response="$(vault_http GET "${path}" || true)"
  value="$(json_get "${response}" data.data.value)"

  if [[ -z "${value}" && "${VAULT_AUTH_METHOD:-token}" == "approle" ]]; then
    unset VAULT_TOKEN || true
    vault_login_approle || true
    response="$(vault_http GET "${path}" || true)"
    value="$(json_get "${response}" data.data.value)"
  fi

  printf '%s' "${value}"
}

# Seed from previous successful runtime fetch to keep services available if Vault is transiently unavailable.
OPENAI_API_KEY="${OPENAI_API_KEY:-$(runtime_get OPENAI_API_KEY)}"
SUPABASE_URL="${SUPABASE_URL:-$(runtime_get SUPABASE_URL)}"
SUPABASE_KEY="${SUPABASE_KEY:-$(runtime_get SUPABASE_KEY)}"
API_TOKEN="${API_TOKEN:-$(runtime_get API_TOKEN)}"
COPILOT_API_KEY="${COPILOT_API_KEY:-$(runtime_get COPILOT_API_KEY)}"
RAG_API_TOKEN="${RAG_API_TOKEN:-$(runtime_get RAG_API_TOKEN)}"
SCENARIO_API_KEY="${SCENARIO_API_KEY:-$(runtime_get SCENARIO_API_KEY)}"
SCENARIO_SUPABASE_URL="${SCENARIO_SUPABASE_URL:-$(runtime_get SCENARIO_SUPABASE_URL)}"
SCENARIO_SUPABASE_KEY="${SCENARIO_SUPABASE_KEY:-$(runtime_get SCENARIO_SUPABASE_KEY)}"

vault_login_approle || true

OPENAI_API_KEY="${OPENAI_API_KEY:-$(fetch_secret_value kv/data/study-agents/openai)}"
SUPABASE_URL="${SUPABASE_URL:-$(fetch_secret_value kv/data/study-agents/supabase-url)}"
SUPABASE_KEY="${SUPABASE_KEY:-$(fetch_secret_value kv/data/study-agents/supabase-key)}"
API_TOKEN="${API_TOKEN:-$(fetch_secret_value kv/data/study-agents/api-token)}"
COPILOT_API_KEY="${COPILOT_API_KEY:-$(fetch_secret_value kv/data/study-agents/copilot-api-key)}"
RAG_API_TOKEN="${RAG_API_TOKEN:-$(fetch_secret_value kv/data/study-agents/rag-api-token)}"
SCENARIO_API_KEY="${SCENARIO_API_KEY:-$(fetch_secret_value kv/data/study-agents/scenario-api-key)}"
SCENARIO_SUPABASE_URL="${SCENARIO_SUPABASE_URL:-$(fetch_secret_value kv/data/study-agents/scenario-supabase-url)}"
SCENARIO_SUPABASE_KEY="${SCENARIO_SUPABASE_KEY:-$(fetch_secret_value kv/data/study-agents/scenario-supabase-key)}"

umask 077
cat > "${ENV_OUT}" <<EOF_ENV
OPENAI_API_KEY=${OPENAI_API_KEY}
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_KEY=${SUPABASE_KEY}
API_TOKEN=${API_TOKEN}
COPILOT_API_KEY=${COPILOT_API_KEY}
RAG_API_TOKEN=${RAG_API_TOKEN}
SCENARIO_API_KEY=${SCENARIO_API_KEY}
SCENARIO_SUPABASE_URL=${SCENARIO_SUPABASE_URL}
SCENARIO_SUPABASE_KEY=${SCENARIO_SUPABASE_KEY}
EOF_ENV
chmod 600 "${ENV_OUT}" || true

set -a
# shellcheck disable=SC1090
. "${ENV_OUT}"
set +a

exec "$@"
