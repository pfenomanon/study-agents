#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PUBLIC_DOMAIN="${1:-}"
ALLOW_CIDR="${2:-}"

if [[ -z "$PUBLIC_DOMAIN" ]]; then
  echo "Usage: bash scripts/configure_lan_https.sh <public-domain-or-ip> [allow-cidr]" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

if [[ -z "$ALLOW_CIDR" ]]; then
  ALLOW_CIDR="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | head -n1 | sed -E 's#([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+/[0-9]+#\1.0/24#')"
fi

if [[ -z "$ALLOW_CIDR" ]]; then
  ALLOW_CIDR="127.0.0.1/32"
fi

upsert_env() {
  local key="$1"
  local value="$2"
  local tmp

  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") { print key "=" value; updated = 1; next }
    { print }
    END { if (!updated) print key "=" value }
  ' .env > "$tmp"
  mv "$tmp" .env
}

upsert_env PUBLIC_DOMAIN "$PUBLIC_DOMAIN"
upsert_env AUTHELIA_OIDC_CLIENT_REDIRECT_URI "https://${PUBLIC_DOMAIN}/oidc/callback"
upsert_env GATEWAY_ALLOWED_CIDRS "127.0.0.1/32 ::1/128 ${ALLOW_CIDR}"

bash scripts/bootstrap_internal_tls.sh
bash scripts/bootstrap_authelia.sh

if docker compose ps >/dev/null 2>&1; then
  docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate authelia tls-gateway
else
  sudo docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate authelia tls-gateway
fi

echo "LAN HTTPS configured for: https://${PUBLIC_DOMAIN}/"
echo "Allowlist CIDRs: 127.0.0.1/32 ::1/128 ${ALLOW_CIDR}"
echo "Next: export CA cert with 'bash scripts/export_caddy_root_ca.sh' and trust it on client devices."
