# Windows Client Quickstart

Use this package on each Windows local machine.

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
