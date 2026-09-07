@echo off
cd /d "%~dp0"
title RTX 5090 Model Cascading Gateway

echo ============================================================
echo   RTX 5090 MODEL CASCADING GATEWAY (32GB VRAM)
echo   Local 5090 (Ollama) ^<--^> Google Gemini Cloud
echo ============================================================
echo.

rem Check if already running on port 8000
netstat -ano | findstr /R /C:"127.0.0.1:8000 *LISTENING" >nul
if %errorlevel% equ 0 (
    echo [NOTICE] RTX 5090 Gateway is ALREADY running on port 8000!
    echo Opening web dashboard in your browser...
    start http://127.0.0.1:8000/
    timeout /t 3 >nul
    exit /b 0
)

echo [START] Launching Gateway on http://127.0.0.1:8000 ...
".venv\Scripts\python.exe" cascade_proxy.py
echo.
echo [NOTICE] Gateway has stopped.
pause
