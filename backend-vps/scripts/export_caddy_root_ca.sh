#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_ROOT_PATH="${1:-$HOME/caddy-local-root.crt}"
OUT_DIR="$(dirname "$OUT_ROOT_PATH")"
OUT_INTERMEDIATE_PATH="${2:-${OUT_DIR}/caddy-local-intermediate.crt}"
OUT_CHAIN_PATH="${3:-${OUT_DIR}/caddy-local-chain.crt}"

mkdir -p "$OUT_DIR"

CADDY_VOLUME=""
if docker volume inspect backend-vps_caddy-data >/dev/null 2>&1; then
  CADDY_VOLUME="backend-vps_caddy-data"
else
  CADDY_VOLUME="$(docker volume ls --format '{{.Name}}' | awk '/_caddy-data$/ {print; exit}')"
fi

if [[ -z "$CADDY_VOLUME" ]]; then
  echo "ERROR: could not find Caddy data volume." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

docker run --rm -v "${CADDY_VOLUME}:/data:ro" alpine \
  sh -lc "cat /data/caddy/pki/authorities/local/root.crt" > "${TMP_DIR}/root.crt"
docker run --rm -v "${CADDY_VOLUME}:/data:ro" alpine \
  sh -lc "cat /data/caddy/pki/authorities/local/intermediate.crt" > "${TMP_DIR}/intermediate.crt"

if [[ ! -s "${TMP_DIR}/root.crt" ]]; then
  echo "ERROR: export failed, missing root certificate in Caddy data." >&2
  exit 1
fi
if [[ ! -s "${TMP_DIR}/intermediate.crt" ]]; then
  echo "ERROR: export failed, missing intermediate certificate in Caddy data." >&2
  exit 1
fi

install -m 644 "${TMP_DIR}/root.crt" "${OUT_ROOT_PATH}"
install -m 644 "${TMP_DIR}/intermediate.crt" "${OUT_INTERMEDIATE_PATH}"
cat "${TMP_DIR}/intermediate.crt" "${TMP_DIR}/root.crt" > "${OUT_CHAIN_PATH}"
chmod 644 "${OUT_CHAIN_PATH}" || true

PUBLIC_DOMAIN="$(awk -F= '$1 == "PUBLIC_DOMAIN" {print substr($0, index($0, $2)); exit}' .env 2>/dev/null || true)"
if [[ -z "${PUBLIC_DOMAIN}" ]]; then
  PUBLIC_DOMAIN="127.0.0.1"
fi

echo "Exported gateway CA files:"
echo "  Root:         ${OUT_ROOT_PATH}"
echo "  Intermediate: ${OUT_INTERMEDIATE_PATH}"
echo "  Chain:        ${OUT_CHAIN_PATH}"

if command -v openssl >/dev/null 2>&1; then
  echo
  echo "Root certificate:"
  openssl x509 -in "${OUT_ROOT_PATH}" -noout -subject -issuer -fingerprint -sha256
  echo
  echo "Intermediate certificate:"
  openssl x509 -in "${OUT_INTERMEDIATE_PATH}" -noout -subject -issuer -fingerprint -sha256
  openssl verify -CAfile "${OUT_ROOT_PATH}" "${OUT_INTERMEDIATE_PATH}" >/dev/null
fi

if docker compose ps --status running --services 2>/dev/null | grep -qx tls-gateway; then
  status_code="$(
    curl -sS --cacert "${OUT_ROOT_PATH}" \
      --resolve "${PUBLIC_DOMAIN}:443:127.0.0.1" \
      -o /dev/null \
      -w '%{http_code}' \
      "https://${PUBLIC_DOMAIN}/healthz" || true
  )"
  if [[ "${status_code}" == "200" ]]; then
    echo
    echo "Gateway trust check: https://${PUBLIC_DOMAIN}/healthz returned HTTP 200 (trusted with exported root)."
  else
    echo
    echo "WARNING: gateway trust check failed for https://${PUBLIC_DOMAIN}/healthz (HTTP ${status_code})." >&2
    echo "Verify PUBLIC_DOMAIN and tls-gateway status, then retry export." >&2
  fi
fi

echo
echo "Windows (PowerShell, run on each client):"
cat <<'EOF'
# Remove stale Caddy local roots/intermediates to avoid CA mismatch after gateway CA rotation.
$stores = @(
  'Cert:\CurrentUser\Root', 'Cert:\CurrentUser\CA',
  'Cert:\LocalMachine\Root', 'Cert:\LocalMachine\CA'
)
foreach ($store in $stores) {
  Get-ChildItem $store | Where-Object { $_.Subject -like '*Caddy Local Authority*' } | Remove-Item -Force
}

# Import the current root + intermediate from this deployment.
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation 'Cert:\CurrentUser\Root'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-intermediate.crt" -CertStoreLocation 'Cert:\CurrentUser\CA'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation 'Cert:\LocalMachine\Root'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-intermediate.crt" -CertStoreLocation 'Cert:\LocalMachine\CA'
EOF
