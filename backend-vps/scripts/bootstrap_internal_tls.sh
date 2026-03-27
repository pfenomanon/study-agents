#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
TLS_DIR="${ROOT_DIR}/docker/internal-tls"
VAULT_TRUST_DIR="${ROOT_DIR}/docker/vault/trust"

CA_KEY="${TLS_DIR}/local-ca.key"
CA_CERT="${TLS_DIR}/local-ca.crt"
VAULT_KEY="${TLS_DIR}/vault.key"
VAULT_CSR="${TLS_DIR}/vault.csr"
VAULT_CERT="${TLS_DIR}/vault.crt"
VAULT_EXT="${TLS_DIR}/vault.ext"
VAULT_CA_PEM="${TLS_DIR}/vault-ca.pem"
AUTHELIA_KEY="${TLS_DIR}/authelia.key"
AUTHELIA_CSR="${TLS_DIR}/authelia.csr"
AUTHELIA_CERT="${TLS_DIR}/authelia.crt"
AUTHELIA_EXT="${TLS_DIR}/authelia.ext"

mkdir -p "${TLS_DIR}"
mkdir -p "${VAULT_TRUST_DIR}"
chmod 755 "${TLS_DIR}" || true

get_env() {
  local key="$1"
  [[ -f "${ENV_FILE}" ]] || return 0
  awk -F= -v key="${key}" '$1 == key {print substr($0, length(key) + 2); exit}' "${ENV_FILE}"
}

if [[ ! -s "${CA_KEY}" || ! -s "${CA_CERT}" ]]; then
  openssl genrsa -out "${CA_KEY}" 4096
  openssl req -x509 -new -nodes -key "${CA_KEY}" -sha256 -days 3650 \
    -subj "/CN=study-agents-internal-ca" \
    -out "${CA_CERT}"
fi

if [[ ! -s "${VAULT_KEY}" || ! -s "${VAULT_CERT}" ]]; then
  PUBLIC_DOMAIN="$(get_env PUBLIC_DOMAIN)"

  openssl genrsa -out "${VAULT_KEY}" 2048
  openssl req -new -key "${VAULT_KEY}" -subj "/CN=vault" -out "${VAULT_CSR}"

  {
    echo "authorityKeyIdentifier=keyid,issuer"
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=serverAuth"
    printf 'subjectAltName=DNS:vault,DNS:localhost,IP:127.0.0.1'
    if [[ -n "${PUBLIC_DOMAIN}" ]]; then
      if [[ "${PUBLIC_DOMAIN}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf ',IP:%s' "${PUBLIC_DOMAIN}"
      else
        printf ',DNS:%s' "${PUBLIC_DOMAIN}"
      fi
    fi
    printf '\n'
  } > "${VAULT_EXT}"

  openssl x509 -req -in "${VAULT_CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" -CAcreateserial \
    -out "${VAULT_CERT}" -days 825 -sha256 -extfile "${VAULT_EXT}"
fi

if [[ ! -s "${AUTHELIA_KEY}" || ! -s "${AUTHELIA_CERT}" ]]; then
  PUBLIC_DOMAIN="$(get_env PUBLIC_DOMAIN)"

  openssl genrsa -out "${AUTHELIA_KEY}" 2048
  openssl req -new -key "${AUTHELIA_KEY}" -subj "/CN=authelia" -out "${AUTHELIA_CSR}"

  {
    echo "authorityKeyIdentifier=keyid,issuer"
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=serverAuth"
    printf 'subjectAltName=DNS:authelia,DNS:localhost,IP:127.0.0.1'
    if [[ -n "${PUBLIC_DOMAIN}" ]]; then
      if [[ "${PUBLIC_DOMAIN}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf ',IP:%s' "${PUBLIC_DOMAIN}"
      else
        printf ',DNS:%s' "${PUBLIC_DOMAIN}"
      fi
    fi
    printf '\n'
  } > "${AUTHELIA_EXT}"

  openssl x509 -req -in "${AUTHELIA_CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" -CAcreateserial \
    -out "${AUTHELIA_CERT}" -days 825 -sha256 -extfile "${AUTHELIA_EXT}"
fi

cp "${CA_CERT}" "${VAULT_CA_PEM}"
cp "${CA_CERT}" "${VAULT_TRUST_DIR}/vault-ca.pem"

chmod 600 "${CA_KEY}" || true
chmod 644 "${VAULT_KEY}" "${VAULT_CERT}" "${AUTHELIA_KEY}" "${AUTHELIA_CERT}" "${CA_CERT}" "${VAULT_CA_PEM}" || true
chmod 644 "${VAULT_TRUST_DIR}/vault-ca.pem" || true
rm -f "${VAULT_CSR}" "${VAULT_EXT}" "${AUTHELIA_CSR}" "${AUTHELIA_EXT}" "${TLS_DIR}/local-ca.srl"

echo "Internal TLS bootstrap complete:"
echo "  CA cert: ${CA_CERT}"
echo "  Vault cert: ${VAULT_CERT}"
echo "  Vault key: ${VAULT_KEY}"
echo "  Authelia cert: ${AUTHELIA_CERT}"
echo "  Authelia key: ${AUTHELIA_KEY}"
