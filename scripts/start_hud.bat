@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=.venv\Scripts\pythonw.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

start "" "%PYTHON_EXE%" -m cascadegateway.hud.overlay
