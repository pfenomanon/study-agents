#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend-vps"

if [[ ! -d "${BACKEND_DIR}" ]]; then
  echo "backend-vps directory not found at ${BACKEND_DIR}" >&2
  exit 1
fi

cd "${BACKEND_DIR}"

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  echo "Created backend-vps/.env from .env.example"
fi

echo "Current directory: ${PWD}"
echo "Next steps:"
echo "  1) Edit .env with your values"
echo "  2) Run: docker compose up -d --build"
echo "  3) Read: ../GETTING_STARTED.md (full) or ../SETUP.md (quick)"

