#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/client_config.sh"

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG. Copy client_config.example.sh to client_config.sh and edit values."
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG"

cd "$SCRIPT_DIR/study-agents"
source .venv/bin/activate

export REMOTE_MODE="remote_image"
REMOTE_IMAGE_URL="${VPS_BASE_URL%/}/cag-ocr-answer"

python -m study_agents.vision_agent \
  --mode remote_image \
  --remote-image-url "$REMOTE_IMAGE_URL" \
  --dpi "$DPI" \
  --top-in "$TOP_IN" \
  --left-in "$LEFT_IN" \
  --right-in "$RIGHT_IN" \
  --bottom-in "$BOTTOM_IN"
