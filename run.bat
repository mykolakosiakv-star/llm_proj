@echo off
rem Starts LLM-AutoBench at http://127.0.0.1:8000 (stop with Ctrl+C). Keep this file ASCII-only: cmd breaks on UTF-8 text.
set PYTHONUTF8=1
cd /d "%~dp0"

set "PY_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=python"

echo LLM-AutoBench: http://127.0.0.1:8000
echo Python: %PY_EXE%
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
"%PY_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
