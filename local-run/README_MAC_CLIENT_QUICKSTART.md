# macOS Client Quickstart

Use this package on each macOS local machine.

## Zero-Python one-liner (VPS does OCR/CAG)

Run from any directory in Terminal:

```bash
bash -lc 'tmp="$(mktemp /tmp/study-agents-remote-capture.XXXXXX.sh)" && curl -fsSL https://raw.githubusercontent.com/pfenomanon/study-agents/main/local-run/native/vision_remote_capture_macos.sh -o "$tmp" && chmod +x "$tmp" && "$tmp" --remote-image-url "https://<your-vps>/cag-ocr-answer" --api-token "<optional-api-token>" --profile-id generic --dpi 96 --top-in 1.0 --left-in 0.5 --right-in 0.5 --bottom-in 1.0 --loop'
```

This path requires no Python and keeps OCR/retrieval/reasoning on the VPS.
It also starts the secure capture session flow by default (session URL + access code + local QR popup page).
If raw download is blocked by repository privacy controls, run the local script directly: `local-run/native/vision_remote_capture_macos.sh`.
Controls: `Z` capture, `Esc`/`Q` quit.
The native macOS script supports global hotkeys when Swift is available; otherwise it falls back to terminal-focused key input.
Optional monitor selection: add `--monitor-index <n>` (1-based).

## Fresh machine one-liner (no dependencies)

```bash
command -v brew >/dev/null 2>&1 || NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; (eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"; brew install git); git clone git@github.com:pfenomanon/study-agents.git; cd study-agents/local-run; chmod +x install_client_macos.sh; ./install_client_macos.sh
```

This repository is private; cloning requires GitHub repo access and an SSH key already configured on the GitHub account.

## If repo is already cloned

From repo root run:

```bash
cd local-run && chmod +x install_client_macos.sh && ./install_client_macos.sh
```

What this does:
- Installs Homebrew if missing
- Installs Git if missing
- Installs Python 3.11 if missing
- Creates `.venv` under `local-run/study-agents`
- Installs client dependencies
- Creates `client_config.sh` from template

## Configure endpoint

Edit:
- `local-run/client_config.sh`

Set:
- `VPS_BASE_URL=https://<your-domain-or-ip>`
- `REMOTE_API_TOKEN=<token>` (only if backend has `API_TOKEN` set)

## Run

Preferred (remote OCR + remote answer):

```bash
cd local-run && ./run_remote_image.sh
```

Fallback (local OCR + remote answer):

```bash
cd local-run && ./run_remote_text.sh
```

Connectivity test:

```bash
cd local-run && ./test_remote_api.sh
```

## macOS permissions

Grant permissions to your terminal app:
- Screen Recording
- Accessibility (recommended when keyboard hooks are used)

Path:
- System Settings -> Privacy & Security
