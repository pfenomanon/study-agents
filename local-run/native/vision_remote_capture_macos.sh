#!/usr/bin/env bash
set -euo pipefail

REMOTE_IMAGE_URL=""
API_TOKEN="${REMOTE_API_TOKEN:-}"
PROFILE_ID="${PROFILE_ID:-}"
PLATFORM=""
MODEL=""
OLLAMA_TARGET=""
DPI="96"
TOP_IN="0"
BOTTOM_IN="0"
LEFT_IN="0"
RIGHT_IN="0"
MONITOR_INDEX="1"
LOOP_MODE="0"
SESSION_WEB="1"
SESSION_WEB_OPEN="1"
SESSION_WEB_QR="1"
SESSION_WEB_TTL_MINUTES="120"
CAPTURE_SESSION_ID=""
GLOBAL_HOTKEYS="1"
GLOBAL_HOTKEYS_AVAILABLE="0"
HOTKEY_FIFO=""
HOTKEY_SWIFT=""
HOTKEY_PID=""
CAPTURE_X=""
CAPTURE_Y=""
CAPTURE_W=""
CAPTURE_H=""
CAPTURE_DISPLAY_ID=""

usage() {
  cat <<'EOF'
Usage:
  vision_remote_capture_macos.sh --remote-image-url URL [options]

Options:
  --remote-image-url URL     Required. VPS /cag-ocr-answer endpoint.
  --api-token TOKEN          Optional X-API-Key token.
  --profile-id ID            Optional profile namespace id.
  --platform openai|ollama   Optional reasoning runtime hint.
  --model NAME               Optional model hint.
  --ollama-target local|cloud
  --dpi N                    Pixels per inch for margin conversion (default: 96).
  --top-in N                 Top margin in inches.
  --left-in N                Left margin in inches.
  --right-in N               Right margin in inches.
  --bottom-in N              Bottom margin in inches.
  --monitor-index N          1-based monitor index (default: 1).
  --loop                     Prompt for repeated captures.
  --no-global-hotkeys        Require terminal focus for key input.
  --no-session-web           Disable secure session bootstrap page.
  --no-session-web-open      Do not auto-open local QR popup page.
  --no-session-web-qr        Disable local QR popup page generation.
  --session-web-ttl-minutes N  Session lifetime in minutes (default: 120).
  -h, --help                 Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote-image-url) REMOTE_IMAGE_URL="${2:-}"; shift 2 ;;
    --api-token) API_TOKEN="${2:-}"; shift 2 ;;
    --profile-id) PROFILE_ID="${2:-}"; shift 2 ;;
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --ollama-target) OLLAMA_TARGET="${2:-}"; shift 2 ;;
    --dpi) DPI="${2:-96}"; shift 2 ;;
    --top-in) TOP_IN="${2:-0}"; shift 2 ;;
    --left-in) LEFT_IN="${2:-0}"; shift 2 ;;
    --right-in) RIGHT_IN="${2:-0}"; shift 2 ;;
    --bottom-in) BOTTOM_IN="${2:-0}"; shift 2 ;;
    --monitor-index) MONITOR_INDEX="${2:-1}"; shift 2 ;;
    --loop) LOOP_MODE="1"; shift ;;
    --no-global-hotkeys) GLOBAL_HOTKEYS="0"; shift ;;
    --no-session-web) SESSION_WEB="0"; shift ;;
    --no-session-web-open) SESSION_WEB_OPEN="0"; shift ;;
    --no-session-web-qr) SESSION_WEB_QR="0"; shift ;;
    --session-web-ttl-minutes) SESSION_WEB_TTL_MINUTES="${2:-120}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$REMOTE_IMAGE_URL" ]]; then
  echo "Missing --remote-image-url" >&2
  usage
  exit 2
fi

if [[ "$REMOTE_IMAGE_URL" == http://* ]]; then
  host_part="$(printf '%s' "$REMOTE_IMAGE_URL" | sed -E 's#^http://([^/:]+).*#\1#')"
  if [[ "$host_part" != "localhost" && "$host_part" != "127.0.0.1" ]]; then
    echo "Warning: --remote-image-url uses HTTP. Use HTTPS for encrypted transport on untrusted networks." >&2
  fi
fi

for cmd in curl screencapture osascript awk; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

cleanup_hotkey_listener() {
  if [[ -n "${HOTKEY_PID:-}" ]]; then
    kill "$HOTKEY_PID" >/dev/null 2>&1 || true
  fi
  [[ -n "${HOTKEY_FIFO:-}" ]] && rm -f "$HOTKEY_FIFO" >/dev/null 2>&1 || true
  [[ -n "${HOTKEY_SWIFT:-}" ]] && rm -f "$HOTKEY_SWIFT" >/dev/null 2>&1 || true
}
trap cleanup_hotkey_listener EXIT

capture_session_start_url() {
  local base
  base="$(printf '%s' "$REMOTE_IMAGE_URL" | sed -E 's#(https?://[^/]+).*#\1#')"
  if [[ -z "$base" || "$base" != http://* && "$base" != https://* ]]; then
    echo "Invalid --remote-image-url: $REMOTE_IMAGE_URL" >&2
    exit 1
  fi
  printf "%s/capture-session/start" "$base"
}

json_get_string() {
  local key="$1"
  local raw="$2"
  printf "%s" "$raw" | tr '\n' ' ' | sed -nE "s/.*\"${key}\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\1/p" | head -n1
}

json_get_bool() {
  local key="$1"
  local raw="$2"
  printf "%s" "$raw" | tr '\n' ' ' | sed -nE "s/.*\"${key}\"[[:space:]]*:[[:space:]]*(true|false).*/\1/p" | head -n1
}

escape_html() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  printf "%s" "$s"
}

escape_js_sq() {
  printf "%s" "$1" | sed -e "s/\\\\/\\\\\\\\/g" -e "s/'/\\\\'/g"
}

write_session_qr_popup() {
  local session_id="$1"
  local access_code="$2"
  local access_url="$3"
  local expires_at="$4"
  local out_dir="${TMPDIR:-/tmp}/study-agents-capture-sessions"
  mkdir -p "$out_dir"
  local html_path="$out_dir/capture_session_${session_id}_qr.html"
  local safe_session safe_code safe_url safe_exp js_url
  safe_session="$(escape_html "$session_id")"
  safe_code="$(escape_html "$access_code")"
  safe_url="$(escape_html "$access_url")"
  safe_exp="$(escape_html "${expires_at:-N/A}")"
  js_url="$(escape_js_sq "$access_url")"
  cat >"$html_path" <<EOF
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Capture Session QR</title>
  <style>
    body { margin: 0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #061229; color: #e6eeff; }
    .wrap { max-width: 680px; margin: 0 auto; padding: 20px; }
    .card { border: 1px solid #2b4778; border-radius: 14px; background: #0d1b33; padding: 16px; }
    .row { margin: 10px 0; }
    .label { font-weight: 700; color: #cfe0ff; margin-bottom: 4px; }
    .value { word-break: break-all; white-space: pre-wrap; }
    #qrcode { margin: 10px auto; width: 300px; min-height: 300px; background: #fff; border-radius: 10px; padding: 12px; display: grid; place-items: center; color: #021126; }
  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js" crossorigin="anonymous"></script>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="row"><div class="label">Session ID</div><div class="value">$safe_session</div></div>
      <div class="row"><div class="label">Access Code</div><div class="value">$safe_code</div></div>
      <div class="row"><div class="label">VPS Session URL</div><div class="value">$safe_url</div></div>
      <div class="row"><div class="label">Expires At (UTC)</div><div class="value">$safe_exp</div></div>
      <div id="qrcode">Loading QR...</div>
    </div>
  </div>
  <script>
    (function () {
      var url = '$js_url';
      var target = document.getElementById('qrcode');
      if (window.QRCode) {
        target.innerHTML = '';
        new QRCode(target, { text: url, width: 280, height: 280, correctLevel: QRCode.CorrectLevel.M });
      } else {
        target.textContent = 'QR unavailable. Use URL + access code.';
      }
    })();
  </script>
</body>
</html>
EOF
  printf "%s" "$html_path"
}

create_remote_capture_session() {
  local start_url payload raw ok session_id access_code access_url expires_at
  start_url="$(capture_session_start_url)"
  payload="{\"ttl_minutes\":${SESSION_WEB_TTL_MINUTES}}"
  local curl_args=(
    -sS
    -X POST "$start_url"
    -H "Content-Type: application/json"
    -d "$payload"
  )
  [[ -n "$API_TOKEN" ]] && curl_args+=(-H "X-API-Key: $API_TOKEN")
  raw="$(curl "${curl_args[@]}")"
  ok="$(json_get_bool "ok" "$raw")"
  session_id="$(json_get_string "session_id" "$raw")"
  access_code="$(json_get_string "access_code" "$raw")"
  access_url="$(json_get_string "access_url" "$raw")"
  expires_at="$(json_get_string "expires_at" "$raw")"
  if [[ "$ok" != "true" || -z "$session_id" || -z "$access_code" || -z "$access_url" ]]; then
    local err
    err="$(json_get_string "error" "$raw")"
    [[ -z "$err" ]] && err="Failed to create capture session."
    echo "$err" >&2
    echo "$raw" >&2
    exit 1
  fi
  CAPTURE_SESSION_ID="$session_id"
  echo "Session report URL (VPS): $access_url"
  echo "Session access code: $access_code"
  [[ -n "$expires_at" ]] && echo "Session expires (UTC): $expires_at"
  if [[ "$SESSION_WEB_QR" == "1" ]]; then
    local qr_html
    qr_html="$(write_session_qr_popup "$session_id" "$access_code" "$access_url" "$expires_at")"
    echo "Session QR page: $qr_html"
    if [[ "$SESSION_WEB_OPEN" == "1" ]]; then
      open "$qr_html" >/dev/null 2>&1 || true
    fi
  fi
}

inches_to_px() {
  local inches="$1"
  awk -v i="$inches" -v d="$DPI" 'BEGIN { printf("%d", (i * d) + 0.5) }'
}

trim() {
  awk '{$1=$1; print}'
}

desktop_bounds() {
  osascript -e 'tell application "Finder" to get bounds of window of desktop'
}

print_result() {
  local raw="$1"
  if command -v jq >/dev/null 2>&1; then
    local q a c
    q="$(printf "%s" "$raw" | jq -r '.question // empty' 2>/dev/null || true)"
    a="$(printf "%s" "$raw" | jq -r '.answer // empty' 2>/dev/null || true)"
    c="$(printf "%s" "$raw" | jq -r 'if (.citations|type)=="array" then (.citations|join(", ")) else empty end' 2>/dev/null || true)"
    [[ -n "$q" ]] && printf "\nQuestion:\n%s\n" "$q"
    [[ -n "$a" ]] && printf "\nAnswer:\n%s\n" "$a"
    [[ -n "$c" ]] && printf "\nCitations:\n%s\n" "$c"
  else
    printf "%s\n" "$raw"
  fi
}

query_monitors() {
  if command -v swift >/dev/null 2>&1; then
    swift -e '
import CoreGraphics
let maxDisplays: UInt32 = 32
var ids = [CGDirectDisplayID](repeating: 0, count: Int(maxDisplays))
var count: UInt32 = 0
let err = CGGetActiveDisplayList(maxDisplays, &ids, &count)
if err == .success {
  for i in 0..<Int(count) {
    let b = CGDisplayBounds(ids[i])
    print("\(i+1)|\(Int(ids[i]))|\(Int(b.origin.x))|\(Int(b.origin.y))|\(Int(b.size.width))|\(Int(b.size.height))")
  }
}
'
    return
  fi

  # Fallback: single desktop bounds if Swift is unavailable.
  local bounds bx by br bb
  bounds="$(desktop_bounds)"
  IFS=',' read -r bx by br bb <<< "$bounds"
  bx="$(printf "%s" "$bx" | trim)"
  by="$(printf "%s" "$by" | trim)"
  br="$(printf "%s" "$br" | trim)"
  bb="$(printf "%s" "$bb" | trim)"
  local w h
  w=$((br - bx))
  h=$((bb - by))
  echo "1|0|$bx|$by|$w|$h"
}

resolve_capture_region() {
  local lines=()
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && lines+=("$line")
  done < <(query_monitors)

  if [[ "${#lines[@]}" -lt 1 ]]; then
    echo "No monitors detected." >&2
    exit 1
  fi

  if ! [[ "$MONITOR_INDEX" =~ ^[0-9]+$ ]]; then
    echo "Invalid --monitor-index: $MONITOR_INDEX" >&2
    exit 2
  fi
  if (( MONITOR_INDEX < 1 || MONITOR_INDEX > ${#lines[@]} )); then
    echo "Invalid --monitor-index $MONITOR_INDEX. Available range: 1..${#lines[@]}" >&2
    exit 2
  fi

  local selected="${lines[$((MONITOR_INDEX - 1))]}"
  local _idx _id _x _y _w _h
  IFS='|' read -r _idx _id _x _y _w _h <<< "$selected"
  CAPTURE_DISPLAY_ID="$_id"

  local top_px left_px right_px bottom_px
  top_px="$(inches_to_px "$TOP_IN")"
  left_px="$(inches_to_px "$LEFT_IN")"
  right_px="$(inches_to_px "$RIGHT_IN")"
  bottom_px="$(inches_to_px "$BOTTOM_IN")"

  CAPTURE_X=$((_x + left_px))
  CAPTURE_Y=$((_y + top_px))
  CAPTURE_W=$((_w - left_px - right_px))
  CAPTURE_H=$((_h - top_px - bottom_px))

  if [[ "$CAPTURE_W" -lt 64 || "$CAPTURE_H" -lt 64 ]]; then
    echo "Invalid capture region after margins. width=$CAPTURE_W height=$CAPTURE_H" >&2
    exit 1
  fi

  echo "MonitorIndex: $MONITOR_INDEX (available=${#lines[@]}, display_id=$CAPTURE_DISPLAY_ID)"
}

start_global_hotkey_listener() {
  if [[ "$GLOBAL_HOTKEYS" != "1" ]]; then
    return
  fi
  if ! command -v swift >/dev/null 2>&1; then
    return
  fi

  HOTKEY_FIFO="$(mktemp -u /tmp/study-agents-hotkey.XXXXXX.fifo)"
  mkfifo "$HOTKEY_FIFO"
  HOTKEY_SWIFT="$(mktemp /tmp/study-agents-hotkey.XXXXXX.swift)"
  cat >"$HOTKEY_SWIFT" <<'EOF'
import CoreGraphics
import Foundation
import Darwin

func down(_ code: CGKeyCode) -> Bool {
  return CGEventSource.keyState(.combinedSessionState, key: code)
}

var prevZ = false
var prevQ = false
var prevEsc = false

while true {
  let z = down(6)   // Z
  let q = down(12)  // Q
  let esc = down(53) // Escape

  if (esc && !prevEsc) || (q && !prevQ) {
    print("exit")
    fflush(stdout)
  } else if z && !prevZ {
    print("capture")
    fflush(stdout)
  }

  prevZ = z
  prevQ = q
  prevEsc = esc
  usleep(35000)
}
EOF

  swift "$HOTKEY_SWIFT" >"$HOTKEY_FIFO" 2>/dev/null &
  HOTKEY_PID=$!
  GLOBAL_HOTKEYS_AVAILABLE="1"
}

wait_for_trigger_focused() {
  while true; do
    IFS= read -rsn1 key
    if [[ "$key" == $'\e' ]]; then
      return 1
    fi
    key_lc="${key,,}"
    if [[ "$key_lc" == "q" ]]; then
      return 1
    fi
    if [[ "$key_lc" == "z" ]]; then
      return 0
    fi
  done
}

wait_for_trigger() {
  if [[ "$GLOBAL_HOTKEYS_AVAILABLE" == "1" && -n "$HOTKEY_FIFO" ]]; then
    local event=""
    if IFS= read -r event <"$HOTKEY_FIFO"; then
      case "$event" in
        capture) return 0 ;;
        exit) return 1 ;;
      esac
    fi
    echo "Warning: global hotkey listener unavailable. Falling back to focused terminal input." >&2
    GLOBAL_HOTKEYS_AVAILABLE="0"
    wait_for_trigger_focused
    return $?
  fi

  wait_for_trigger_focused
  return $?
}

echo "Mode: remote_image (native macOS client)"
echo "Endpoint: $REMOTE_IMAGE_URL"
[[ -n "$PROFILE_ID" ]] && echo "Profile: $PROFILE_ID"
resolve_capture_region
echo "DPI: $DPI, Margins(in): top=$TOP_IN left=$LEFT_IN right=$RIGHT_IN bottom=$BOTTOM_IN"

start_global_hotkey_listener
if [[ "$GLOBAL_HOTKEYS_AVAILABLE" == "1" ]]; then
  echo "Global hotkeys active: press 'Z' to capture from any focused app. Press 'Esc' or 'Q' to quit."
else
  echo "Press 'Z' to capture. Press 'Esc' or 'Q' to quit."
fi

if [[ "$SESSION_WEB" == "1" ]]; then
  create_remote_capture_session
fi

while true; do
  if ! wait_for_trigger; then
    break
  fi

  echo
  echo "[$(date -u +%H:%M:%S)] Capturing monitor $MONITOR_INDEX..."
  tmp_png="$(mktemp /tmp/study-agents-capture.XXXXXX.png)"
  trap 'rm -f "$tmp_png"' EXIT
  screencapture -x -R "${CAPTURE_X},${CAPTURE_Y},${CAPTURE_W},${CAPTURE_H}" "$tmp_png"

  curl_args=(
    -sS
    -X POST "$REMOTE_IMAGE_URL"
    -F "image=@${tmp_png};type=image/png"
  )
  [[ -n "$API_TOKEN" ]] && curl_args+=(-H "X-API-Key: $API_TOKEN")
  [[ -n "$PROFILE_ID" ]] && curl_args+=(-F "profile_id=$PROFILE_ID")
  [[ -n "$PLATFORM" ]] && curl_args+=(-F "platform=$PLATFORM")
  [[ -n "$MODEL" ]] && curl_args+=(-F "model=$MODEL")
  [[ -n "$OLLAMA_TARGET" ]] && curl_args+=(-F "ollama_target=$OLLAMA_TARGET")
  [[ -n "$CAPTURE_SESSION_ID" ]] && curl_args+=(-F "capture_session_id=$CAPTURE_SESSION_ID")

  echo "[$(date -u +%H:%M:%S)] Uploading image to VPS..."
  raw_response="$(curl "${curl_args[@]}")"
  echo "[$(date -u +%H:%M:%S)] Capture complete."
  print_result "$raw_response"

  rm -f "$tmp_png"
  trap - EXIT

  if [[ "$LOOP_MODE" != "1" ]]; then
    break
  fi
done
