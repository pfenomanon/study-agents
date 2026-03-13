@echo off
setlocal
call "%~dp0client_config.bat"
cd /d "%~dp0study-agents"
call .venv\Scripts\activate.bat

python -c "import requests, os; url=os.environ.get('VPS_BASE_URL','').rstrip('/')+'/cag-answer'; token=os.environ.get('REMOTE_API_TOKEN','').strip(); headers={'X-API-Key':token} if token else {}; payload={'question':'Connectivity test: respond with OK.'}; r=requests.post(url,json=payload,headers=headers,timeout=60); print('Status:',r.status_code); print(r.text[:800])"

endlocal
