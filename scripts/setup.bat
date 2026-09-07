@echo off
setlocal enabledelayedexpansion
title Model Cascade Gateway - Setup
cd /d "%~dp0"

echo ============================================================
echo   MODEL CASCADE GATEWAY - AUTOMATED SETUP
echo   Hardware-Adaptive Local LLM ^<--^> Cloud Cascading
echo ============================================================
echo.

rem 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from https://python.org and check "Add to PATH".
    pause
    exit /b 1
)
echo [OK] Python detected.

rem 2. Check Ollama
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Ollama is not running on http://127.0.0.1:11434!
    echo Make sure Ollama is installed from https://ollama.com and running.
) else (
    echo [OK] Ollama is running.
)

rem 3. Create virtual environment
if not exist ".venv" (
    echo [SETUP] Creating Python virtual environment in .venv ...
    python -m venv .venv
)

rem 4. Install dependencies
echo [SETUP] Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\pip.exe" install -r requirements.txt

rem 5. Setup .env
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [OK] Created .env from .env.example.
    )
)

rem 6. Create shortcuts
echo [SETUP] Generating desktop and startup shortcuts ...
powershell -ExecutionPolicy Bypass -File "create_shortcuts.ps1"

echo.
echo ============================================================
echo   SETUP COMPLETE!
echo   Run "start_cascade.bat" or launch from your Desktop shortcut.
echo ============================================================
echo.
pause
