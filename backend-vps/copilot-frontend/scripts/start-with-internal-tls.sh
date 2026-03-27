#!/usr/bin/env sh
set -eu

runtime_env_file="/env/.env.runtime"
if [ -z "${COPILOT_API_KEY:-}" ]; then
  i=0
  while [ "${i}" -lt 20 ]; do
    if [ -f "${runtime_env_file}" ]; then
      COPILOT_API_KEY="$(grep -m1 '^COPILOT_API_KEY=' "${runtime_env_file}" | cut -d= -f2- || true)"
      if [ -z "${COPILOT_API_KEY:-}" ]; then
        COPILOT_API_KEY="$(grep -m1 '^API_TOKEN=' "${runtime_env_file}" | cut -d= -f2- || true)"
      fi
      if [ -n "${COPILOT_API_KEY:-}" ]; then
        export COPILOT_API_KEY
        break
      fi
    fi
    i=$((i + 1))
    sleep 1
  done
fi

if [ -f "${runtime_env_file}" ] && [ -z "${API_TOKEN:-}" ]; then
  API_TOKEN="$(grep -m1 '^API_TOKEN=' "${runtime_env_file}" | cut -d= -f2- || true)"
  if [ -n "${API_TOKEN:-}" ]; then
    export API_TOKEN
  fi
fi

# Bind externally so gateway/health checks can reach the frontend directly.
HOSTNAME=0.0.0.0 PORT=3000 node /app/server.js &
NODE_PID="$!"

cleanup() {
  kill -TERM "${NODE_PID}" >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

if [ -f /tls/copilot-frontend.crt ] && [ -f /tls/copilot-frontend.key ]; then
  caddy run --config /app/Caddyfile.internal --adapter caddyfile
else
  echo "warning: /tls/copilot-frontend.{crt,key} not found; running frontend without internal TLS sidecar" >&2
  wait "${NODE_PID}"
fi
