# Windows Client Quickstart

Use this package on each Windows local machine.

## 1) Extract and open Command Prompt

```cmd
study-agents-windows-client-20260311-025842.zip
```

Extract the ZIP, then open Command Prompt in the extracted folder.

## 2) Install everything

```cmd
install_client.bat
```

## 3) Configure endpoint

Edit:
- `client_config.bat`

Set:
- `VPS_BASE_URL=http://<your-vps-ip>:8000`
- `REMOTE_API_TOKEN=<token>` (only if backend has `API_TOKEN` set)

## 4) Run

Preferred:

```cmd
run_remote_image.bat
```

Fallback (local OCR, remote answer):

```cmd
run_remote_text.bat
```

Connectivity test:

```cmd
test_remote_api.bat
```

Controls:
- `Z` capture
- `Esc` quit
