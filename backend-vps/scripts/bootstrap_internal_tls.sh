#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TLS_DIR="${ROOT_DIR}/docker/internal-tls"
SUPABASE_CERT_DIR="${ROOT_DIR}/supabase/certs"

CA_KEY="${TLS_DIR}/internal-ca.key"
CA_CERT="${TLS_DIR}/internal-ca.crt"
CA_SERIAL="${TLS_DIR}/internal-ca.srl"
CA_BUNDLE="${TLS_DIR}/ca-bundle.crt"

CA_SUBJECT="/CN=study-agents-internal-ca"
CA_DAYS="${INTERNAL_TLS_CA_DAYS:-3650}"
LEAF_DAYS="${INTERNAL_TLS_LEAF_DAYS:-825}"

mkdir -p "${TLS_DIR}" "${SUPABASE_CERT_DIR}"

log() {
  echo "==> $*"
}

is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

ensure_ca() {
  if [[ -s "${CA_KEY}" && -s "${CA_CERT}" ]]; then
    return 0
  fi

  log "Generating internal certificate authority..."
  openssl req \
    -x509 \
    -newkey rsa:4096 \
    -sha256 \
    -days "${CA_DAYS}" \
    -nodes \
    -subj "${CA_SUBJECT}" \
    -keyout "${CA_KEY}" \
    -out "${CA_CERT}"

  chmod 600 "${CA_KEY}"
  chmod 644 "${CA_CERT}"
}

build_system_plus_internal_bundle() {
  local system_bundle=""
  for candidate in \
    /etc/ssl/certs/ca-certificates.crt \
    /etc/pki/tls/certs/ca-bundle.crt \
    /etc/ssl/ca-bundle.pem
  do
    if [[ -f "${candidate}" ]]; then
      system_bundle="${candidate}"
      break
    fi
  done

  if [[ -n "${system_bundle}" ]]; then
    cat "${system_bundle}" "${CA_CERT}" > "${CA_BUNDLE}"
  else
    cat "${CA_CERT}" > "${CA_BUNDLE}"
  fi
  chmod 644 "${CA_BUNDLE}"
}

render_extfile() {
  local extfile="$1"
  shift
  local sans=("$@")
  local san_csv
  san_csv="$(IFS=,; echo "${sans[*]}")"

  cat > "${extfile}" <<EOF_EXT
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=${san_csv}
EOF_EXT
}

issue_leaf() {
  local name="$1"
  shift
  local sans=("$@")

  local key="${TLS_DIR}/${name}.key"
  local csr="${TLS_DIR}/${name}.csr"
  local crt="${TLS_DIR}/${name}.crt"
  local ext="${TLS_DIR}/${name}.ext"

  if [[ -s "${key}" && -s "${crt}" ]]; then
    return 0
  fi

  log "Issuing certificate: ${name}"
  render_extfile "${ext}" "${sans[@]}"

  openssl req \
    -new \
    -newkey rsa:2048 \
    -nodes \
    -subj "/CN=${name}" \
    -keyout "${key}" \
    -out "${csr}"

  openssl x509 \
    -req \
    -in "${csr}" \
    -CA "${CA_CERT}" \
    -CAkey "${CA_KEY}" \
    -CAcreateserial \
    -CAserial "${CA_SERIAL}" \
    -out "${crt}" \
    -days "${LEAF_DAYS}" \
    -sha256 \
    -extfile "${ext}"

  rm -f "${csr}" "${ext}"
  chmod 644 "${key}"
  chmod 644 "${crt}"
}

detect_gateway_ips() {
  local ip
  docker network inspect backend-vps_backend --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
  docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
}

main() {
  ensure_ca

  issue_leaf "cag-service" \
    "DNS:cag-service" "DNS:localhost" "IP:127.0.0.1"
  issue_leaf "rag-service" \
    "DNS:rag-service" "DNS:localhost" "IP:127.0.0.1"
  issue_leaf "copilot-service" \
    "DNS:copilot-service" "DNS:localhost" "IP:127.0.0.1"
  issue_leaf "copilot-frontend" \
    "DNS:copilot-frontend" "DNS:localhost" "IP:127.0.0.1"
  issue_leaf "authelia" \
    "DNS:authelia" "DNS:localhost" "IP:127.0.0.1"
  issue_leaf "redis" \
    "DNS:redis" "DNS:localhost" "IP:127.0.0.1"

  local supabase_sans=("DNS:host.docker.internal" "DNS:localhost" "IP:127.0.0.1")
  while read -r ip; do
    if [[ -n "${ip}" ]] && is_ipv4 "${ip}"; then
      supabase_sans+=("IP:${ip}")
    fi
  done < <(detect_gateway_ips)

  issue_leaf "supabase-api" "${supabase_sans[@]}"

  cp "${TLS_DIR}/supabase-api.crt" "${SUPABASE_CERT_DIR}/api-cert.pem"
  cp "${TLS_DIR}/supabase-api.key" "${SUPABASE_CERT_DIR}/api-key.pem"
  cp "${CA_CERT}" "${SUPABASE_CERT_DIR}/internal-ca.crt"
  chmod 644 "${SUPABASE_CERT_DIR}/api-cert.pem" "${SUPABASE_CERT_DIR}/internal-ca.crt"
  chmod 600 "${SUPABASE_CERT_DIR}/api-key.pem"

  build_system_plus_internal_bundle

  log "Internal TLS assets are ready."
  log "  CA: ${CA_CERT}"
  log "  Bundle: ${CA_BUNDLE}"
  log "  Supabase certs: ${SUPABASE_CERT_DIR}"
}

main "$@"
