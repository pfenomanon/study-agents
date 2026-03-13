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
SUPA_KEY=""

STATUS_JSON="$(supabase status -o json 2>/dev/null || supabase status --json 2>/dev/null || true)"
if [[ -n "$STATUS_JSON" ]]; then
  if read -r SUPA_URL SUPA_KEY <<<"$(python - <<'PY' || true
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
if not url or not anon_key:
    sys.exit(1)
print(url, anon_key)
PY
)"; then
    :
  else
    SUPA_URL=""
    SUPA_KEY=""
  fi
fi

if [[ -z "$SUPA_URL" || -z "$SUPA_KEY" ]]; then
  STATUS_ENV="$(supabase status -o env \
      --override-name api.url=SUPABASE_URL \
      --override-name api.rest_url=SUPABASE_URL \
      --override-name api.anon_key=SUPABASE_KEY \
      --override-name api.rest_anon_key=SUPABASE_KEY 2>/dev/null || true)"
  if [[ -n "$STATUS_ENV" ]]; then
    SUPA_URL="$(printf '%s\n' "$STATUS_ENV" | grep '^SUPABASE_URL=' | head -n1 | cut -d= -f2-)"
    SUPA_KEY="$(printf '%s\n' "$STATUS_ENV" | grep '^SUPABASE_KEY=' | head -n1 | cut -d= -f2-)"
  fi
fi

if [[ -z "$SUPA_URL" || -z "$SUPA_KEY" ]]; then
  echo "Could not determine Supabase URL/key from status output."
  exit 1
fi

echo "Supabase REST URL: $SUPA_URL"
echo "Supabase anon key: $SUPA_KEY"

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
