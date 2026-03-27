#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "==> $*"
}

run_docker() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo docker "$@"
  else
    echo "ERROR: Docker not accessible and sudo is unavailable." >&2
    exit 1
  fi
}

run_sudo() {
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    "$@"
  fi
}

log "Disk before cleanup:"
df -h /
echo

log "Docker usage before cleanup:"
run_docker system df || true
echo

log "Pruning Docker build cache..."
run_docker builder prune -af || true

log "Pruning unused Docker images..."
run_docker image prune -af || true

log "Pruning stopped containers..."
run_docker container prune -f || true

log "Cleaning apt cache..."
run_sudo apt-get clean || true

log "Vacuuming old journal logs..."
run_sudo journalctl --vacuum-time=3d >/dev/null 2>&1 || true

echo
log "Disk after cleanup:"
df -h /
echo

log "Docker usage after cleanup:"
run_docker system df || true
