#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

install_if_missing() {
  local pkg="$1"
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "==> Installing $pkg..."
    apt-get install -y "$pkg"
  else
    echo "==> $pkg already installed."
  fi
}

detect_repo_dir() {
  local search_dirs=("$BASE_DIR" "$SCRIPT_DIR")
  for candidate in "${search_dirs[@]}"; do
    if [[ -d "$candidate/src" && -f "$candidate/docker-compose.yml" ]]; then
      echo "$candidate"
      return
    fi
  done

  local zip_candidate=""
  for dir in "${search_dirs[@]}"; do
    for path in "$dir"/study-agents-*.zip "$dir"/dist/study-agents-*.zip; do
      [[ -f "$path" ]] || continue
      zip_candidate="$path"
      break 2
    done
  done

  if [[ -z "$zip_candidate" ]]; then
    echo "Could not find repo files or a study-agents-*.zip archive. Please place this script (or the zip) in the same directory." >&2
    exit 1
  fi

  local target="/home/study-agents"
  printf "==> Unpacking %s to %s...\n" "$zip_candidate" "$target" >&2
  rm -rf "$target"
  mkdir -p "$target"

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  unzip -q "$zip_candidate" -d "$tmp_dir"

  local unpack_src="$tmp_dir"
  if [[ -d "$tmp_dir/study-agents" ]]; then
    unpack_src="$tmp_dir/study-agents"
  fi

  shopt -s dotglob
  mv "$unpack_src"/* "$target"/
  shopt -u dotglob
  rm -rf "$tmp_dir"

  printf "%s" "$target"
}

echo "==> Updating apt cache..."
apt-get update -y

install_if_missing curl
install_if_missing ca-certificates
install_if_missing gnupg
install_if_missing lsb-release
install_if_missing unzip

ROOT_DIR="$(detect_repo_dir)"
cd "$ROOT_DIR"

echo "==> Ensuring Docker Engine is installed..."
if ! command -v docker >/dev/null 2>&1; then
  install_if_missing docker.io
  systemctl enable --now docker
else
  echo "==> Docker already installed."
fi

echo "==> Ensuring Docker Compose plugin is installed..."
if ! docker compose version >/dev/null 2>&1; then
  if ! apt-get install -y docker-compose-plugin; then
    apt-get install -y docker-compose-v2
  fi
else
  echo "==> Docker Compose already available."
fi

install_supabase_cli() {
  if command -v supabase >/dev/null 2>&1; then
    echo "==> Supabase CLI already installed."
    return
  fi

  echo "==> Installing Supabase CLI via official installer..."
  if curl -fsSL https://app.supabase.com/api/install/cli | sh; then
    export PATH="$HOME/.supabase/bin:$PATH"
    return
  fi

  echo "==> Official installer failed. Downloading fallback binary..."
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
      echo "Unsupported architecture for Supabase CLI fallback installer: ${arch}" >&2
      exit 1
      ;;
  esac
  tmp_dir="$(mktemp -d)"
  curl -fL "https://github.com/supabase/cli/releases/latest/download/${asset}" \
    -o "$tmp_dir/supabase.tar.gz"
  tar -xzf "$tmp_dir/supabase.tar.gz" -C "$tmp_dir"
  install -m 755 "$tmp_dir/supabase" /usr/local/bin/supabase
  rm -rf "$tmp_dir"
}

echo "==> Installing Supabase CLI if needed..."
install_supabase_cli

echo "==> Running Supabase setup..."
chmod +x scripts/setup_local_supabase.sh
PATH="$HOME/.supabase/bin:$PATH" scripts/setup_local_supabase.sh

ensure_port_free() {
  local port="$1"
  command -v docker >/dev/null 2>&1 || return 0
  local ids
  if ! ids="$(docker ps --filter "publish=${port}" -q 2>/dev/null)"; then
    return 0
  fi
  if [[ -n "$ids" ]]; then
    echo "==> Port ${port} is in use. Stopping containers: $ids"
    docker stop $ids >/dev/null
    docker rm $ids >/dev/null
  fi
}

echo "==> Resetting existing study-agents containers..."
docker compose down --remove-orphans >/dev/null 2>&1 || true
ensure_port_free 8000
ensure_port_free 8100

echo "==> Building and starting Docker services..."
docker compose up -d --build

echo "Bootstrap complete. Services:"
docker compose ps
