#!/usr/bin/env python3
"""Build reproducible backend (VPS) and frontend (Windows client) bundles."""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TS = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

BACKEND_BASENAME = f"study-agents-backend-vps-{TS}"
CLIENT_BASENAME = f"study-agents-windows-client-{TS}"

BACKEND_ARCHIVE = DIST / f"{BACKEND_BASENAME}.tar.gz"
CLIENT_ARCHIVE = DIST / f"{CLIENT_BASENAME}.zip"
MASTER_GUIDE = DIST / f"DEPLOYMENT-QUICKSTART-{TS}.md"

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "__pycache__",
    "dist",
}
EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db"}
EXCLUDE_PATH_PREFIXES = {
    Path("docker/authelia"),
    Path("docker/internal-tls"),
    Path("docker/vault/bootstrap"),
    Path("docker/vault/data"),
    Path("data"),
    Path("knowledge_graph"),
    Path("research_output"),
    Path("temp_images"),
    Path("supabase/.temp"),
}


def _has_prefix(path: Path, prefix: Path) -> bool:
    return path == prefix or prefix in path.parents


def _is_excluded_rel(rel_path: Path) -> bool:
    return any(_has_prefix(rel_path, prefix) for prefix in EXCLUDE_PATH_PREFIXES)


def _copy_item(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore_names)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _add_dir_to_zip(zip_path: Path, src_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in src_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(src_dir))


def _add_dir_to_targz(tar_path: Path, src_dir: Path) -> None:
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src_dir, arcname=src_dir.name)


def _ignore_names(path: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    path_obj = Path(path)
    try:
        rel_root = path_obj.relative_to(ROOT)
    except ValueError:
        rel_root = Path(".")

    for name in names:
        p = Path(path) / name
        rel = rel_root / name if str(rel_root) != "." else Path(name)
        if name == ".env" or _is_excluded_rel(rel):
            ignored.add(name)
            continue
        if name in EXCLUDE_DIRS or name.endswith(".egg-info"):
            ignored.add(name)
            continue
        if p.is_file():
            if p.suffix in EXCLUDE_FILE_SUFFIXES or name in EXCLUDE_FILE_NAMES:
                ignored.add(name)
    return ignored


def _build_backend_bundle(workdir: Path) -> None:
    backend_root = workdir / BACKEND_BASENAME
    backend_root.mkdir(parents=True, exist_ok=True)

    for rel in (
        ".env.example",
        "docker-compose.yml",
        "pyproject.toml",
        "README.md",
        "DEPLOYMENT.md",
        "supabase_schema.sql",
    ):
        _copy_item(ROOT / rel, backend_root / rel)

    for rel in ("src", "prompts", "docker", "scripts"):
        _copy_item(ROOT / rel, backend_root / rel)

    backend_quickstart = f"""# Backend VPS Quickstart

Use this package on a new Debian/Ubuntu VPS.

## 1) Extract on VPS

```bash
tar -xzf {BACKEND_ARCHIVE.name}
cd {BACKEND_BASENAME}
```

## 2) Install host dependencies

```bash
bash scripts/install_backend_vps.sh deps
```

## 3) Configure `.env`

```bash
cp -n .env.example .env
nano .env
```

Required values:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY` (service-role key recommended)

Token defaults (important):
- `API_REQUIRE_TOKEN=true`
- `RAG_REQUIRE_TOKEN=true`
- `COPILOT_REQUIRE_TOKEN=true`

If required tokens are empty, the installer auto-generates and writes:
- `API_TOKEN`
- `RAG_API_TOKEN`
- `COPILOT_API_KEY`
- `SCENARIO_API_KEY`

## 4) Apply Supabase schema

Cloud Supabase (recommended):
- Open Supabase SQL Editor.
- Run `supabase_schema.sql`.

CLI path (optional):
- Set `SUPABASE_DB_URL` in `.env` (Postgres DSN), then run:

```bash
bash scripts/install_backend_vps.sh apply-schema
```

## 5) Start backend services

```bash
bash scripts/install_backend_vps.sh start
```

## 6) Verify and monitor

```bash
bash scripts/install_backend_vps.sh status
bash scripts/install_backend_vps.sh logs
```

Default local ports (localhost-bound):
- `127.0.0.1:8000` (`/cag-answer`, `/cag-ocr-answer`)
- `127.0.0.1:8100` (`/build`)
- `127.0.0.1:9010` (`/copilot/*`)
- `127.0.0.1:3000` (Copilot UI)

## Optional: local Supabase all-in-one mode

```bash
bash scripts/install_backend_vps.sh start-local-all
```
"""
    _write_text(backend_root / "README_BACKEND_VPS_QUICKSTART.md", backend_quickstart)
    _add_dir_to_targz(BACKEND_ARCHIVE, backend_root)


def _build_windows_client_bundle(workdir: Path) -> None:
    client_root = workdir / CLIENT_BASENAME
    client_root.mkdir(parents=True, exist_ok=True)
    project_root = client_root / "study-agents"
    project_root.mkdir(parents=True, exist_ok=True)

    for rel in ("src", "prompts", "pyproject.toml", ".env.example", "README.md"):
        _copy_item(ROOT / rel, project_root / rel)

    # Keep a renamed project readme in bundle root for convenience.
    _copy_item(ROOT / "README.md", client_root / "PROJECT_README.md")

    install_client_bat = r"""@echo off
setlocal

cd /d "%~dp0\study-agents"

echo [1/5] Checking Python launcher...
where py >nul 2>nul
if %ERRORLEVEL% neq 0 (
  echo Python not found. Attempting install via winget...
  where winget >nul 2>nul
  if %ERRORLEVEL% neq 0 (
    echo winget not found. Install Python 3.10+ manually, then rerun this script.
    exit /b 1
  )
  winget install -e --id Python.Python.3.11
)

echo [2/5] Creating virtual environment...
py -3.11 -m venv .venv
if %ERRORLEVEL% neq 0 (
  echo Failed creating .venv. Trying default Python...
  py -m venv .venv
  if %ERRORLEVEL% neq 0 (
    echo Failed creating .venv
    exit /b 1
  )
)

echo [3/5] Activating venv...
call .venv\Scripts\activate.bat

echo [4/5] Installing dependencies...
python -m pip install --upgrade pip setuptools wheel
pip install -e .[vision-client]
if %ERRORLEVEL% neq 0 (
  echo Dependency install failed.
  exit /b 1
)

echo [5/5] Creating config files...
if not exist .env copy .env.example .env >nul
if not exist ..\client_config.bat copy ..\client_config.example.bat ..\client_config.bat >nul

echo.
echo Install complete.
echo Next:
echo   1) Edit client_config.bat with your VPS URL and optional token
echo   2) Run run_remote_image.bat
endlocal
"""

    client_config_example = r"""@echo off
REM VPS base URL (no trailing slash)
set VPS_BASE_URL=https://your-domain.example

REM Optional API token (leave empty if backend API_TOKEN is not set)
set REMOTE_API_TOKEN=

REM Capture margins in inches
set DPI=96
set TOP_IN=1.5
set LEFT_IN=0.5
set RIGHT_IN=0.5
set BOTTOM_IN=1.5
"""

    run_remote_image_bat = r"""@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

set REMOTE_MODE=remote_image
set REMOTE_IMAGE_URL=%VPS_BASE_URL%/cag-ocr-answer

python -m study_agents.vision_agent --mode remote_image --remote-image-url %REMOTE_IMAGE_URL% --dpi %DPI% --top-in %TOP_IN% --left-in %LEFT_IN% --right-in %RIGHT_IN% --bottom-in %BOTTOM_IN%
endlocal
"""

    run_remote_text_bat = r"""@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

set REMOTE_MODE=remote
set REMOTE_CAG_URL=%VPS_BASE_URL%/cag-answer

python -m study_agents.vision_agent --mode remote --remote-cag-url %REMOTE_CAG_URL% --dpi %DPI% --top-in %TOP_IN% --left-in %LEFT_IN% --right-in %RIGHT_IN% --bottom-in %BOTTOM_IN%
endlocal
"""

    test_remote_api_bat = r"""@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

python -c "import requests, os; url=os.environ.get('VPS_BASE_URL','').rstrip('/')+'/cag-answer'; token=os.environ.get('REMOTE_API_TOKEN','').strip(); headers={'X-API-Key':token} if token else {}; payload={'question':'Connectivity test: respond with OK.'}; r=requests.post(url,json=payload,headers=headers,timeout=60); print('Status:',r.status_code); print(r.text[:800])"

endlocal
"""

    quickstart = f"""# Windows Client Quickstart

Use this package on each Windows local machine.

## 1) Extract and open Command Prompt

```cmd
{CLIENT_ARCHIVE.name}
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
- `VPS_BASE_URL=https://<your-domain>`
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
"""

    _write_text(client_root / "install_client.bat", install_client_bat)
    _write_text(client_root / "client_config.example.bat", client_config_example)
    _write_text(client_root / "run_remote_image.bat", run_remote_image_bat)
    _write_text(client_root / "run_remote_text.bat", run_remote_text_bat)
    _write_text(client_root / "test_remote_api.bat", test_remote_api_bat)
    _write_text(client_root / "README_WINDOWS_CLIENT_QUICKSTART.md", quickstart)

    _add_dir_to_zip(CLIENT_ARCHIVE, client_root)


def _write_master_guide() -> None:
    content = f"""# Deployment Quickstart ({TS})

Artifacts generated:
- `{BACKEND_ARCHIVE.name}` (backend package for VPS)
- `{CLIENT_ARCHIVE.name}` (frontend/client package for Windows locals)

## Backend on a new VPS

1. Copy backend archive to VPS and extract:
```bash
scp {BACKEND_ARCHIVE.name} user@<vps-ip>:/opt/
ssh user@<vps-ip>
cd /opt
tar -xzf {BACKEND_ARCHIVE.name}
cd {BACKEND_BASENAME}
```
2. Install and configure:
```bash
bash scripts/install_backend_vps.sh deps
cp -n .env.example .env
bash scripts/generate_local_api_keys.sh --write-env
nano .env
```
3. Apply schema:
- Cloud: run `supabase_schema.sql` in Supabase SQL Editor.
- Optional CLI path: set `SUPABASE_DB_URL` in `.env`, then run:
```bash
bash scripts/install_backend_vps.sh apply-schema
```
4. Start:
```bash
bash scripts/install_backend_vps.sh start
```

## Client on a new Windows machine

1. Copy and extract `{CLIENT_ARCHIVE.name}`.
2. In Command Prompt:
```cmd
install_client.bat
```
3. Configure:
```cmd
notepad client_config.bat
```
4. Run:
```cmd
run_remote_image.bat
```
"""
    _write_text(MASTER_GUIDE, content)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="study-agents-bundles-") as tmp:
        workdir = Path(tmp)
        _build_backend_bundle(workdir)
        _build_windows_client_bundle(workdir)
    _write_master_guide()

    print(f"Backend bundle: {BACKEND_ARCHIVE}")
    print(f"Windows client bundle: {CLIENT_ARCHIVE}")
    print(f"Quickstart guide: {MASTER_GUIDE}")


if __name__ == "__main__":
    main()
