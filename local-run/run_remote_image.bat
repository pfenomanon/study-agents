@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

set REMOTE_MODE=remote_image
set REMOTE_IMAGE_URL=%VPS_BASE_URL%/cag-ocr-answer

python -m study_agents.vision_agent --mode remote_image --remote-image-url %REMOTE_IMAGE_URL% --dpi %DPI% --top-in %TOP_IN% --left-in %LEFT_IN% --right-in %RIGHT_IN% --bottom-in %BOTTOM_IN%
endlocal
