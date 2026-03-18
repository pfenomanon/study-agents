# Windows Client Quickstart

Use this package on each Windows local machine.

## Zero-Python one-liner (VPS does OCR/CAG)

Run from any directory in PowerShell:

```powershell
$u='https://raw.githubusercontent.com/pfenomanon/study-agents/main/local-run/native/vision_remote_capture_windows.ps1'; $p=Join-Path $env:TEMP 'study-agents-remote-capture.ps1'; Invoke-WebRequest -UseBasicParsing $u -OutFile $p; & $p -RemoteImageUrl 'https://<your-vps>/cag-ocr-answer' -ApiToken '<optional-api-token>' -ProfileId 'generic' -Dpi 96 -TopIn 1.0 -LeftIn 0.5 -RightIn 0.5 -BottomIn 1.0 -Loop
```

This path requires no Python and keeps OCR/retrieval/reasoning on the VPS.
It also starts the secure capture session flow by default (session URL + access code + local QR popup page).
If raw download is blocked by repository privacy controls, run the local script directly: `local-run/native/vision_remote_capture_windows.ps1`.
Controls: `Z` capture, `Esc`/`Q` quit.

## 1) Open a terminal

Use Command Prompt or PowerShell.

## 2) Fresh machine one-liner (no dependencies)

PowerShell:

```powershell
winget install --id Git.Git -e --source winget; git clone git@github.com:pfenomanon/study-agents.git; cd study-agents\local-run; cmd /c install_client.bat
```

`install_client.bat` attempts to install Python 3.11 via `winget` if missing, creates `.venv`, and installs dependencies.
This repository is private; cloning requires GitHub repo access and an SSH key already configured on the GitHub account.

If the repo is already cloned, use:

```cmd
cd local-run && install_client.bat
```

## 3) Configure endpoint

Edit:
- `client_config.bat`

Set:
- `VPS_BASE_URL=https://<your-vps-domain-or-ip>`
- `REMOTE_API_TOKEN=<token>` (only if backend has `API_TOKEN` set)

## 4) Run

Preferred:

```cmd
cd local-run && run_remote_image.bat
```

Fallback (local OCR, remote answer):

```cmd
cd local-run && run_remote_text.bat
```

Connectivity test:

```cmd
cd local-run && test_remote_api.bat
```

Controls:
- `Z` capture
- `Esc` quit
