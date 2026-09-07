#!/usr/bin/env bash
set -e

echo "============================================================"
echo "  MODEL CASCADE GATEWAY - SETUP (Linux / macOS)"
echo "============================================================"
echo ""

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed!"
    exit 1
fi
echo "[OK] Python 3 detected."

# 2. Check Ollama
if ! curl -s http://127.0.0.1:11434/api/tags &> /dev/null; then
    echo "[WARN] Ollama is not running on http://127.0.0.1:11434. Please start Ollama."
else
    echo "[OK] Ollama is running."
fi

# 3. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[SETUP] Creating virtual environment ..."
    python3 -m venv .venv
fi

# 4. Install dependencies
echo "[SETUP] Installing Python dependencies ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 5. Environment config
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "[OK] Created .env from .env.example."
fi

echo ""
echo "============================================================"
echo "  SETUP COMPLETE!"
echo "  Run: .venv/bin/python cascade_proxy.py"
echo "  Dashboard: http://127.0.0.1:8000/"
echo "============================================================"
