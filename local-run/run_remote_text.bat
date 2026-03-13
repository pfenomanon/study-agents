@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

set REMOTE_MODE=remote
set REMOTE_CAG_URL=%VPS_BASE_URL%/cag-answer

python -m study_agents.vision_agent --mode remote --remote-cag-url %REMOTE_CAG_URL% --dpi %DPI% --top-in %TOP_IN% --left-in %LEFT_IN% --right-in %RIGHT_IN% --bottom-in %BOTTOM_IN%
endlocal
