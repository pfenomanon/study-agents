#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

log() {
  echo "==> $*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

export PATH="$HOME/.local/bin:$HOME/.supabase/bin:$PATH"

install_supabase_cli() {
  if command -v supabase >/dev/null 2>&1; then
    return 0
  fi

  log "Supabase CLI not found. Installing..."

  # Primary installer (may fail on some hosts).
  if curl -fsSL https://app.supabase.com/api/install/cli | sh; then
    export PATH="$HOME/.supabase/bin:$PATH"
  fi

  if command -v supabase >/dev/null 2>&1; then
    return 0
  fi

  # Fallback installer from GitHub release.
  local arch asset tmp_dir
  arch="$(uname -m)"
  case "$arch" in
    x86_64)
      asset="supabase_linux_amd64.tar.gz"
      ;;
    aarch64|arm64)
      asset="supabase_linux_arm64.tar.gz"
      ;;
    *)
      die "Unsupported architecture for Supabase CLI: ${arch}"
      ;;
  esac

  tmp_dir="$(mktemp -d)"
  mkdir -p "$HOME/.local/bin"
  curl -fL "https://github.com/supabase/cli/releases/latest/download/${asset}" -o "${tmp_dir}/supabase.tar.gz"
  tar -xzf "${tmp_dir}/supabase.tar.gz" -C "${tmp_dir}"
  install -m 755 "${tmp_dir}/supabase" "$HOME/.local/bin/supabase"
  rm -rf "${tmp_dir}"

  export PATH="$HOME/.local/bin:$PATH"
  command -v supabase >/dev/null 2>&1 || die "Supabase CLI installation failed."
}

ensure_supabase_project() {
  if [[ -f "supabase/config.toml" ]]; then
    return 0
  fi
  log "Initializing local Supabase project config..."
  supabase init --yes
}

start_supabase() {
  log "Starting local Supabase (minimal profile)..."
  if ! supabase start \
    -x studio \
    -x realtime \
    -x storage-api \
    -x imgproxy \
    -x edge-runtime \
    -x logflare \
    -x vector \
    -x postgres-meta \
    -x supavisor \
    -x mailpit; then
    log "Minimal start failed, retrying full supabase start..."
    supabase start
  fi
}

upsert_env() {
  local key="$1"
  local value="$2"
  local tmp

  [[ -f .env ]] || touch .env

  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") { print key "=" value; updated = 1; next }
    { print }
    END { if (!updated) print key "=" value }
  ' .env > "$tmp"
  mv "$tmp" .env
}

extract_status_value() {
  local key="$1"
  local status_env="$2"
  printf '%s\n' "$status_env" | awk -F= -v key="$key" '$1 == key {print $2; exit}' | tr -d '"'
}

install_supabase_cli
ensure_supabase_project
bash scripts/bootstrap_internal_tls.sh
start_supabase

STATUS_ENV="$(supabase status -o env 2>/dev/null || supabase status --env 2>/dev/null || true)"
[[ -n "$STATUS_ENV" ]] || die "Could not read 'supabase status -o env'."

SUPA_API_URL="$(extract_status_value API_URL "$STATUS_ENV")"
SUPA_DB_URL="$(extract_status_value DB_URL "$STATUS_ENV")"
SUPA_SERVICE_KEY="$(extract_status_value SERVICE_ROLE_KEY "$STATUS_ENV")"
SUPA_SECRET_KEY="$(extract_status_value SECRET_KEY "$STATUS_ENV")"
SUPA_ANON_KEY="$(extract_status_value ANON_KEY "$STATUS_ENV")"

[[ -n "$SUPA_API_URL" ]] || die "Could not determine API_URL from Supabase status output."
[[ -n "$SUPA_DB_URL" ]] || die "Could not determine DB_URL from Supabase status output."

SUPA_KEY=""
if [[ -n "$SUPA_SERVICE_KEY" ]]; then
  SUPA_KEY="$SUPA_SERVICE_KEY"
elif [[ -n "$SUPA_SECRET_KEY" ]]; then
  SUPA_KEY="$SUPA_SECRET_KEY"
elif [[ -n "$SUPA_ANON_KEY" ]]; then
  SUPA_KEY="$SUPA_ANON_KEY"
else
  die "Could not determine a usable Supabase key from status output."
fi

SUPA_API_PORT="$(printf '%s' "$SUPA_API_URL" | sed -E 's#^https?://[^:/]+:([0-9]+).*$#\1#')"
if [[ ! "$SUPA_API_PORT" =~ ^[0-9]+$ ]]; then
  SUPA_API_PORT="54321"
fi

SUPA_CONTAINER_URL="https://host.docker.internal:${SUPA_API_PORT}"

upsert_env SUPABASE_URL "$SUPA_CONTAINER_URL"
upsert_env SUPABASE_KEY "$SUPA_KEY"
upsert_env SUPABASE_DB_URL "$SUPA_DB_URL"

echo "Supabase API URL for backend containers: $SUPA_CONTAINER_URL"
echo "Supabase DB URL: $SUPA_DB_URL"
if [[ "$SUPA_KEY" == "$SUPA_SERVICE_KEY" ]]; then
  echo "Supabase key mode: service_role"
else
  echo "Supabase key mode: non-service (limited)"
fi
echo ".env updated with local Supabase values."
echo "Next step: psql \"$SUPA_DB_URL\" -v ON_ERROR_STOP=1 -f supabase_schema.sql"
