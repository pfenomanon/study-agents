#!/usr/bin/env sh
set -eu

# Force Next.js standalone server to bind loopback so Caddy can always reach it.
HOSTNAME=127.0.0.1 PORT=3000 node /app/server.js &
NODE_PID="$!"

cleanup() {
  kill -TERM "${NODE_PID}" >/dev/null 2>&1 || true
}
trap cleanup INT TERM EXIT

caddy run --config /app/Caddyfile.internal --adapter caddyfile
