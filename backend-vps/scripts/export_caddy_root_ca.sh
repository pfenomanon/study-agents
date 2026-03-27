#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_PATH="${1:-$HOME/caddy-local-root.crt}"
OUT_DIR="$(dirname "$OUT_PATH")"
OUT_FILE="$(basename "$OUT_PATH")"

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

docker run --rm -v "${CADDY_VOLUME}:/data:ro" -v "${OUT_DIR}:/out" alpine \
  sh -lc "cp /data/caddy/pki/authorities/local/root.crt /out/${OUT_FILE}"

if [[ ! -f "$OUT_PATH" ]]; then
  echo "ERROR: export failed, file not found: $OUT_PATH" >&2
  exit 1
fi

if ! chmod 600 "$OUT_PATH" 2>/dev/null; then
  if command -v sudo >/dev/null 2>&1; then
    sudo chown "$(id -u):$(id -g)" "$OUT_PATH" || true
    chmod 600 "$OUT_PATH" || true
  fi
fi

echo "Exported: $OUT_PATH"
if ! openssl x509 -in "$OUT_PATH" -noout -subject -issuer -fingerprint -sha256; then
  if command -v sudo >/dev/null 2>&1; then
    sudo openssl x509 -in "$OUT_PATH" -noout -subject -issuer -fingerprint -sha256 || true
  else
    echo "WARNING: unable to inspect certificate metadata (permission issue)." >&2
  fi
fi

echo
echo "Windows (PowerShell) import command:"
echo "Import-Certificate -FilePath \"$OUT_PATH\" -CertStoreLocation Cert:\\CurrentUser\\Root"
