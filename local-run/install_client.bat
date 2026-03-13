@echo off
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
pip install -e .[vision]
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
