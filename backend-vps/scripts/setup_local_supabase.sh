#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v supabase >/dev/null 2>&1 || {
  echo "Supabase CLI not found. Installing..."
  curl -fsSL https://app.supabase.com/api/install/cli | sh
  export PATH="$HOME/.supabase/bin:$PATH"
}

export PATH="$HOME/.supabase/bin:$PATH"

echo "Starting Supabase stack (this may take a minute)..."
supabase start

SUPA_URL=""
SUPA_SERVICE_KEY=""
SUPA_ANON_KEY=""
SUPA_KEY=""

STATUS_JSON="$(supabase status -o json 2>/dev/null || supabase status --json 2>/dev/null || true)"
if [[ -n "$STATUS_JSON" ]]; then
  if read -r SUPA_URL SUPA_SERVICE_KEY SUPA_ANON_KEY <<<"$(python - <<'PY' || true
import json, os, sys

try:
    data = json.loads(os.environ.get("STATUS_JSON", "{}"))
except json.JSONDecodeError:
    sys.exit(1)

api = data.get("services", {}).get("api", {})
url = (
    api.get("rest_url")
    or api.get("api_url")
    or api.get("url")
    or api.get("restUrl")
    or ""
)
anon_key = api.get("rest_anon_key") or api.get("anon_key") or api.get("restAnonKey") or ""
service_role_key = (
    api.get("service_role_key")
    or api.get("serviceRoleKey")
    or api.get("service_key")
    or api.get("serviceKey")
    or ""
)
if not url:
    sys.exit(1)
print(url, service_role_key, anon_key)
PY
)"; then
    :
  else
    SUPA_URL=""
    SUPA_SERVICE_KEY=""
    SUPA_ANON_KEY=""
  fi
fi

if [[ -z "$SUPA_URL" ]]; then
  STATUS_ENV="$(supabase status -o env \
      --override-name api.url=SUPABASE_URL \
      --override-name api.rest_url=SUPABASE_URL \
      --override-name api.service_role_key=SUPABASE_SERVICE_KEY \
      --override-name api.serviceRoleKey=SUPABASE_SERVICE_KEY \
      --override-name api.anon_key=SUPABASE_KEY \
      --override-name api.rest_anon_key=SUPABASE_KEY 2>/dev/null || true)"
  if [[ -n "$STATUS_ENV" ]]; then
    SUPA_URL="$(printf '%s\n' "$STATUS_ENV" | grep '^SUPABASE_URL=' | head -n1 | cut -d= -f2-)"
    SUPA_SERVICE_KEY="$(printf '%s\n' "$STATUS_ENV" | grep '^SUPABASE_SERVICE_KEY=' | head -n1 | cut -d= -f2-)"
    SUPA_ANON_KEY="$(printf '%s\n' "$STATUS_ENV" | grep '^SUPABASE_KEY=' | head -n1 | cut -d= -f2-)"
  fi
fi

if [[ -z "$SUPA_URL" ]]; then
  echo "Could not determine Supabase URL from status output."
  exit 1
fi

if [[ -n "$SUPA_SERVICE_KEY" ]]; then
  SUPA_KEY="$SUPA_SERVICE_KEY"
elif [[ -n "$SUPA_ANON_KEY" ]]; then
  SUPA_KEY="$SUPA_ANON_KEY"
else
  echo "Could not determine Supabase key from status output."
  exit 1
fi

echo "Supabase REST URL: $SUPA_URL"
if [[ -n "$SUPA_SERVICE_KEY" ]]; then
  echo "Supabase key mode: service_role (recommended for backend ingestion/writes)"
else
  echo "Supabase key mode: anon (limited). Consider setting SUPABASE_KEY to service_role manually for full capabilities."
fi

ENV_FILE="$ROOT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo ".env not found; creating a new one."
  touch "$ENV_FILE"
fi

python - "$ENV_FILE" "$SUPA_URL" "$SUPA_KEY" <<'PY'
import sys, pathlib

path = pathlib.Path(sys.argv[1])
url = sys.argv[2]
key = sys.argv[3]

lines = path.read_text().splitlines()
out = []
found_url = False
found_key = False
for line in lines:
    if line.startswith("SUPABASE_URL="):
        out.append(f"SUPABASE_URL={url}")
        found_url = True
    elif line.startswith("SUPABASE_KEY="):
        out.append(f"SUPABASE_KEY={key}")
        found_key = True
    else:
        out.append(line)
if not found_url:
    out.append(f"SUPABASE_URL={url}")
if not found_key:
    out.append(f"SUPABASE_KEY={key}")
path.write_text("\n".join(out) + "\n")
PY

echo ".env updated with local Supabase credentials."
echo "Local Supabase Studio is typically available at http://127.0.0.1:54323 (check supabase start output)."
