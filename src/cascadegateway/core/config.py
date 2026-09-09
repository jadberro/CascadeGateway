"""
CascadeGateway Core Configuration & Locking
Handles paths, config.yaml loading, environment variables, and single-instance locks.
"""

import os
import sys
import socket
import ctypes
import atexit
from pathlib import Path
from typing import Dict, Any

import yaml
from dotenv import load_dotenv

# Path resolution: src/cascadegateway/core/config.py -> parents[3] is project root
CORE_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = CORE_DIR.parent
SRC_DIR = PACKAGE_DIR.parent
BASE_DIR = SRC_DIR.parent

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = BASE_DIR / "config.yaml"
PID_FILE = DATA_DIR / "gateway.pid"
METRICS_FILE = DATA_DIR / "metrics.json"

ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


config = load_config()

MUTEX_NAME = "Global\\RTX5090_CASCADE_GATEWAY_MUTEX"
SENTINEL_PORT = 18000
_mutex_handle = None
_sentinel_socket = None


def acquire_single_instance_lock() -> bool:
    """Ensures only one instance of CascadeGateway runs at a time."""
    global _mutex_handle, _sentinel_socket
    already_running = False

    # 1. Windows Named Mutex (ctypes, zero external dependencies)
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            _mutex_handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
            ERROR_ALREADY_EXISTS = 183
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                already_running = True
        except Exception:
            pass

    # 2. Cross-Platform Local Sentinel Port
    try:
        _sentinel_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sentinel_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _sentinel_socket.bind(("127.0.0.1", SENTINEL_PORT))
    except OSError:
        already_running = True

    if already_running:
        print("\n[NOTICE] Another instance of Model Cascade Gateway is ALREADY running!")
        print("Opening existing dashboard in your browser...")
        try:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8000/")
        except Exception:
            pass
        sys.exit(0)

    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    return True


def cleanup_single_instance_lock():
    global _mutex_handle, _sentinel_socket
    if _mutex_handle:
        try:
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
    if _sentinel_socket:
        try:
            _sentinel_socket.close()
        except Exception:
            pass
    try:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


atexit.register(cleanup_single_instance_lock)
