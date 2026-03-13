#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/client_config.sh"

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG. Copy client_config.example.sh to client_config.sh and edit values."
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG"

cd "$SCRIPT_DIR/study-agents"
source .venv/bin/activate

python - <<'PY'
import json
import os
import requests

base = os.environ.get("VPS_BASE_URL", "").rstrip("/")
if not base:
    raise SystemExit("VPS_BASE_URL is not set in client_config.sh")

token = os.environ.get("REMOTE_API_TOKEN", "").strip()
headers = {"Content-Type": "application/json"}
if token:
    headers["X-API-Key"] = token

payload = {"question": "Connectivity test: respond with OK."}
resp = requests.post(f"{base}/cag-answer", headers=headers, json=payload, timeout=60)
print("Status:", resp.status_code)
try:
    print(json.dumps(resp.json(), indent=2)[:1200])
except Exception:
    print(resp.text[:1200])
PY
