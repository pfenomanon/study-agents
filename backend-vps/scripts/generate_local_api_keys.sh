#!/usr/bin/env bash
set -euo pipefail

# Generate local backend service API keys/tokens for .env usage.
# Default format is URL-safe tokens (recommended for headers and .env).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
FORMAT="urlsafe"
BYTES="${BYTES:-32}"
WRITE_ENV=0
OVERWRITE=0

KEYS=(
  API_TOKEN
  RAG_API_TOKEN
  COPILOT_API_KEY
  SCENARIO_API_KEY
)

usage() {
  cat <<'EOF'
Usage: bash scripts/generate_local_api_keys.sh [options]

Options:
  --write-env           Write generated keys into .env (ENV_FILE can override path)
  --overwrite           Replace existing non-empty values when used with --write-env
  --format <name>       urlsafe (default) or hex
  --bytes <n>           Random bytes per key (default: 32)
  -h, --help            Show help

Examples:
  bash scripts/generate_local_api_keys.sh
  bash scripts/generate_local_api_keys.sh --write-env
  bash scripts/generate_local_api_keys.sh --write-env --overwrite --format hex
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write-env)
      WRITE_ENV=1
      shift
      ;;
    --overwrite)
      OVERWRITE=1
      shift
      ;;
    --format)
      FORMAT="${2:-}"
      shift 2
      ;;
    --bytes)
      BYTES="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

[[ "${FORMAT}" == "urlsafe" || "${FORMAT}" == "hex" ]] || die "--format must be urlsafe or hex"
[[ "${BYTES}" =~ ^[0-9]+$ ]] || die "--bytes must be an integer"
(( BYTES >= 16 )) || die "--bytes must be >= 16"

gen_key() {
  local format="$1"
  local bytes="$2"
  if [[ "${format}" == "hex" ]]; then
    openssl rand -hex "${bytes}"
  else
    python3 - <<PY
import secrets
print(secrets.token_urlsafe(${bytes}))
PY
  fi
}

upsert_env_key() {
  local file="$1"
  local key="$2"
  local value="$3"

  if [[ ! -f "${file}" ]]; then
    touch "${file}"
  fi

  if grep -q "^${key}=" "${file}"; then
    if [[ "${OVERWRITE}" -eq 0 ]]; then
      local current
      current="$(awk -F= -v k="${key}" '$1 == k {print substr($0, index($0, $2)); exit}' "${file}")"
      if [[ -n "${current}" ]]; then
        echo "skip ${key}: existing non-empty value (use --overwrite to replace)"
        return 0
      fi
    fi
    sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
  else
    printf "%s=%s\n" "${key}" "${value}" >> "${file}"
  fi
}

echo "Token profile:"
echo "- format: ${FORMAT}"
echo "- random bytes per key: ${BYTES}"
echo "- keys: ${KEYS[*]}"
echo

for key in "${KEYS[@]}"; do
  value="$(gen_key "${FORMAT}" "${BYTES}")"
  len="$(printf "%s" "${value}" | wc -c | tr -d ' ')"
  printf "%s=%s\n" "${key}" "${value}"
  printf "  length=%s chars\n" "${len}"
  if [[ "${WRITE_ENV}" -eq 1 ]]; then
    upsert_env_key "${ENV_FILE}" "${key}" "${value}"
  fi
done

if [[ "${WRITE_ENV}" -eq 1 ]]; then
  echo
  echo "Updated ${ENV_FILE}"
fi
