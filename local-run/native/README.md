# Native Remote Vision Capture (No Python)

These scripts capture a local screenshot and send it to your VPS `cag-service` endpoint:

- `POST /cag-ocr-answer`

All OCR + retrieval + reasoning stay on the VPS side.
By default, both scripts also start a secure capture session (`/capture-session/start`),
print the session URL + access code, generate a local QR popup page, and attach
`capture_session_id` to each uploaded capture.
Capture trigger behavior:
- Press `Z` to capture/send current screen region.
- Press `Esc` or `Q` to quit.
- Windows only: use `-MonitorIndex <n>` (1-based) to select the display.
- Windows listens for hotkeys globally (terminal focus not required).

## Windows One-Liner (PowerShell, from any directory)

```powershell
$u='https://raw.githubusercontent.com/pfenomanon/study-agents/main/local-run/native/vision_remote_capture_windows.ps1'; $p=Join-Path $env:TEMP 'study-agents-remote-capture.ps1'; Invoke-WebRequest -UseBasicParsing $u -OutFile $p; & $p -RemoteImageUrl 'https://<your-vps>/cag-ocr-answer' -ApiToken '<optional-api-token>' -ProfileId 'generic' -Dpi 96 -TopIn 1.0 -LeftIn 0.5 -RightIn 0.5 -BottomIn 1.0 -Loop
```

## macOS One-Liner (Terminal, from any directory)

```bash
bash -lc 'tmp="$(mktemp /tmp/study-agents-remote-capture.XXXXXX.sh)" && curl -fsSL https://raw.githubusercontent.com/pfenomanon/study-agents/main/local-run/native/vision_remote_capture_macos.sh -o "$tmp" && chmod +x "$tmp" && "$tmp" --remote-image-url "https://<your-vps>/cag-ocr-answer" --api-token "<optional-api-token>" --profile-id generic --dpi 96 --top-in 1.0 --left-in 0.5 --right-in 0.5 --bottom-in 1.0 --loop'
```

## Private Repository Note

If `raw.githubusercontent.com` access fails due repository privacy, copy these scripts from your internal distribution package and run them directly:

- `local-run/native/vision_remote_capture_windows.ps1`
- `local-run/native/vision_remote_capture_macos.sh`

## Session/TLS Notes

- Session controls are enabled by default.
- To disable session bootstrap, use:
  - Windows: `-NoSessionWeb`
  - macOS: `--no-session-web`
- Transport encryption depends on your URL:
  - `https://...` => encrypted in transit (recommended)
  - `http://...` => not encrypted in transit except loopback/local-only usage
