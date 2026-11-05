@echo off
chcp 65001 >nul
set PYTHONUTF8=1
echo ============================================================================
echo   Запуск програмно-дослідницького комплексу «LLM-AutoBench» v1.0
echo   Тема: Дослідження ефективності застосування великих мовних моделей
echo         для автоматизації відповідей на типові запити користувачів
echo ============================================================================
echo.

set "PY_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY_EXE%" (
    set "PY_EXE=python"
)

echo Використовується інтерпретатор: %PY_EXE%
echo Відкрийте у браузері: http://127.0.0.1:8000
echo.
start "" http://127.0.0.1:8000
"%PY_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
