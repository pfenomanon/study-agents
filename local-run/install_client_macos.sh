#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/study-agents"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Missing $PROJECT_DIR"
  exit 1
fi

ensure_brew() {
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi

  echo "[1/6] Homebrew not found. Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

ensure_python311() {
  if command -v python3.11 >/dev/null 2>&1; then
    return 0
  fi

  echo "[3/7] Installing python@3.11 with Homebrew..."
  brew install python@3.11
}

ensure_git() {
  if command -v git >/dev/null 2>&1; then
    return 0
  fi

  echo "[4/7] Installing git with Homebrew..."
  brew install git
}

echo "[1/7] Checking Homebrew..."
ensure_brew

if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

echo "[2/7] Checking Python 3.11..."
ensure_python311
echo "[4/7] Checking git..."
ensure_git

PY_BIN="$(command -v python3.11 || true)"
if [[ -z "$PY_BIN" ]]; then
  PY_BIN="$(command -v python3 || true)"
fi
if [[ -z "$PY_BIN" ]]; then
  echo "Python was not found after installation."
  exit 1
fi

echo "[5/7] Creating virtual environment..."
cd "$PROJECT_DIR"
"$PY_BIN" -m venv .venv

echo "[6/7] Installing dependencies..."
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e .[vision]

echo "[7/7] Creating local config files..."
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
if [[ ! -f "$SCRIPT_DIR/client_config.sh" ]]; then
  cp "$SCRIPT_DIR/client_config.example.sh" "$SCRIPT_DIR/client_config.sh"
fi
chmod +x "$SCRIPT_DIR/client_config.sh"

echo "Done."
echo
echo "Next:"
echo "  1) Edit local-run/client_config.sh (set VPS_BASE_URL and optional REMOTE_API_TOKEN)"
echo "  2) macOS privacy settings:"
echo "     - System Settings -> Privacy & Security -> Screen Recording (allow Terminal/iTerm)"
echo "     - System Settings -> Privacy & Security -> Accessibility (allow Terminal/iTerm if keyboard hook is used)"
echo "  3) Run: ./run_remote_image.sh"
