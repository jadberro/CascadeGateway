"""
Windows System Tray Desktop Controller for CascadeGateway
Lives silently in the notification tray next to the clock.
Allows live control of Biasing Modes, Model Selection, Startup on Boot, Game Mode (VRAM Purge), and Web Dashboard.
"""

import sys
import io

# Fix pythonw.exe windowless execution: sys.stdout and sys.stderr are None
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

import os
import time
import json
import socket
import threading
import webbrowser
import urllib.request
from pathlib import Path
from typing import Optional

from PIL import Image
import pystray
from pystray import MenuItem as item, Menu
import uvicorn

from cascadegateway.core.config import config, BASE_DIR, DATA_DIR, acquire_single_instance_lock
from cascadegateway.core.router import (
    BIASING_STATE,
    WORKFLOW_STATE,
    MODEL_SELECTION_STATE,
    METRICS,
)
from cascadegateway.api.server import app

STARTUP_DIR = Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
STARTUP_LNK = STARTUP_DIR / "RTX5090Cascade.lnk"
ICON_PATH = BASE_DIR / "assets" / "icon.png"

server_instance: Optional[uvicorn.Server] = None
server_thread: Optional[threading.Thread] = None


def is_port_in_use(port: int = 8000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_uvicorn_server():
    global server_instance
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8000)

    uvicorn_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,
        use_colors=False,
        loop="asyncio"
    )
    server_instance = uvicorn.Server(uvicorn_config)
    server_instance.run()


def launch_server_thread():
    global server_thread
    server_thread = threading.Thread(target=start_uvicorn_server, daemon=False)
    server_thread.start()


def is_startup_enabled() -> bool:
    return STARTUP_LNK.exists()


def toggle_startup(icon, item):
    try:
        if is_startup_enabled():
            STARTUP_LNK.unlink(missing_ok=True)
            icon.notify("Windows Startup: Disabled", "CascadeGateway")
        else:
            ps_script = BASE_DIR / "scripts" / "create_shortcuts.ps1"
            if not ps_script.exists():
                ps_script = BASE_DIR / "create_shortcuts.ps1"
            if ps_script.exists():
                os.system(f'powershell.exe -ExecutionPolicy Bypass -File "{ps_script}"')
            icon.notify("Windows Startup: Enabled (Launches at boot)", "CascadeGateway")
    except Exception as e:
        icon.notify(f"Error toggling startup: {e}", "CascadeGateway")


def open_dashboard(icon, item):
    webbrowser.open("http://127.0.0.1:8000/")


def launch_hud_action(icon, item):
    try:
        import subprocess
        venv_pythonw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
        py_bin = str(venv_pythonw) if venv_pythonw.exists() else sys.executable
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen([py_bin, "-m", "cascadegateway.hud.overlay"], cwd=str(BASE_DIR), creationflags=flags)
    except Exception as e:
        icon.notify(f"Failed to launch HUD: {e}", "CascadeGateway")


def free_vram_action(icon, item):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/models/unload",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode())
            unloaded = data.get("unloaded_models", [])
            freed_count = len(unloaded)
            icon.notify(
                f"🎮 Game Mode: Unloaded {freed_count} model(s) from VRAM. GPU is fully cleared for gaming!",
                "CascadeGateway"
            )
    except Exception as e:
        icon.notify(f"Failed to free VRAM: {e}", "CascadeGateway")


def preload_vram_action(icon, item):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/models/preload",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            data = json.loads(resp.read().decode())
            if data.get("success"):
                model = data.get("model", "Model")
                icon.notify(
                    f"⚡ {model} loaded and pinned in VRAM!",
                    "CascadeGateway"
                )
            else:
                icon.notify(f"Preload notice: {data.get('error')}", "CascadeGateway")
    except Exception as e:
        icon.notify(f"Failed to preload VRAM: {e}", "CascadeGateway")



def set_bias_mode(mode: str, factor: float = 0.35):
    def action(icon, item):
        BIASING_STATE["mode"] = mode
        BIASING_STATE["bias_factor"] = factor
        mode_names = {
            "adaptive": "Adaptive Mode (Auto-scales with budget)",
            "manual": f"Manual Mode ({int(factor*100)}% Local Bias)",
            "local_only": "Strict 100% Local Mode ($0 tokens)",
        }
        icon.notify(f"Switched to {mode_names.get(mode, mode)}", "CascadeGateway")
    return action


def is_bias_mode_active(mode: str, factor: float = None):
    def check(item):
        if BIASING_STATE["mode"] != mode:
            return False
        if factor is not None and abs(BIASING_STATE["bias_factor"] - factor) > 0.05:
            return False
        return True
    return check


def set_workflow_mode_action(mode: str):
    def action(icon, item):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/v1/workflow/mode",
                data=json.dumps({"mode": mode}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode())
                mode_info = data.get("info", {})
                icon.notify(f"Switched to {mode_info.get('icon', '')} {mode_info.get('name', mode)}", "CascadeGateway")
        except Exception as e:
            icon.notify(f"Failed to set mode: {e}", "CascadeGateway")
    return action


def is_workflow_mode_active(mode: str):
    def check(item):
        return WORKFLOW_STATE.get("active_mode") == mode
    return check


def set_model_selection_tray(role: str, model_name: str):
    def action(icon, item):
        MODEL_SELECTION_STATE[role] = model_name
        icon.notify(f"{role.capitalize()} Model set to: {model_name}", "CascadeGateway")
    return action


def is_model_selected(role: str, model_name: str) -> bool:
    return MODEL_SELECTION_STATE.get(role, "auto") == model_name


def auto_configure_ides_action(icon, item):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/ide/auto-config",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
            if data.get("continue_configured"):
                icon.notify("Auto-configured Continue (~/.continue/config.json)!", "CascadeGateway")
            else:
                details = "; ".join(data.get("details", []))
                icon.notify(f"IDE Config: {details}", "CascadeGateway")
    except Exception as e:
        icon.notify(f"Auto-config failed: {e}", "CascadeGateway")


def get_stats_text(item):
    saved = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    dollars = (saved / 1_000_000.0) * 3.00
    reqs = METRICS["total_requests"]
    offload = 0.0
    if reqs > 0:
        offload = (METRICS["local_5090_requests"] / reqs) * 100.0
    return f"Saved Today: ${dollars:.2f} ({saved:,} tokens - {offload:.0f}% offload)"


def restart_server(icon, item):
    global server_instance
    if server_instance:
        server_instance.should_exit = True
    time.sleep(1.0)
    launch_server_thread()
    icon.notify("Gateway server restarted on port 8000.", "CascadeGateway")


def exit_action(icon, item):
    global server_instance
    if server_instance:
        server_instance.should_exit = True
    icon.stop()
    sys.exit(0)


def create_menu():
    return Menu(
        item("CascadeGateway (Port 8000)", lambda icon, item: None, enabled=False),
        item("🖥️ Open Always-On-Top HUD", launch_hud_action),
        item("Open Web Dashboard", open_dashboard, default=True),
        item("⚡ Auto-Configure Continue / Cursor", auto_configure_ides_action),
        item("🛑 Pause & Free GPU (Unload VRAM)", free_vram_action),
        item("▶️ Resume & Preload GPU (Warm VRAM)", preload_vram_action),
        Menu.SEPARATOR,
        item("Workflow Mode", Menu(
            item("🧠 Architect & Builder (Review Gate)", set_workflow_mode_action("architect"), checked=is_workflow_mode_active("architect"), radio=True),
            item("🛡️ Asymmetric Verification (/verify)", set_workflow_mode_action("verify"), checked=is_workflow_mode_active("verify"), radio=True),
            item("🚀 Solo Sprint (Direct Fast Coder)", set_workflow_mode_action("solo"), checked=is_workflow_mode_active("solo"), radio=True),
            item("🌐 Deep Context (Gemini + Local)", set_workflow_mode_action("deep_context"), checked=is_workflow_mode_active("deep_context"), radio=True),
            item("🔬 Math & Algo Proof", set_workflow_mode_action("algo"), checked=is_workflow_mode_active("algo"), radio=True),
        )),
        item("Architect Model", Menu(
            item("Auto-Detect", lambda icon, item: set_model_selection_tray("architect", "auto")(icon, item), checked=lambda item: is_model_selected("architect", "auto"), radio=True),
            item("🧠 deepseek-r1:14b (CoT)", lambda icon, item: set_model_selection_tray("architect", "deepseek-r1:14b")(icon, item), checked=lambda item: is_model_selected("architect", "deepseek-r1:14b"), radio=True),
            item("🏛️ gemma4:26b (General)", lambda icon, item: set_model_selection_tray("architect", "gemma4:26b")(icon, item), checked=lambda item: is_model_selected("architect", "gemma4:26b"), radio=True),
            item("⚡ qwen2.5-coder:32b", lambda icon, item: set_model_selection_tray("architect", "qwen2.5-coder:32b")(icon, item), checked=lambda item: is_model_selected("architect", "qwen2.5-coder:32b"), radio=True),
        )),
        item("Builder Model", Menu(
            item("Auto-Detect", lambda icon, item: set_model_selection_tray("builder", "auto")(icon, item), checked=lambda item: is_model_selected("builder", "auto"), radio=True),
            item("⚡ qwen2.5-coder:32b (Primary)", lambda icon, item: set_model_selection_tray("builder", "qwen2.5-coder:32b")(icon, item), checked=lambda item: is_model_selected("builder", "qwen2.5-coder:32b"), radio=True),
            item("🧠 deepseek-r1:14b", lambda icon, item: set_model_selection_tray("builder", "deepseek-r1:14b")(icon, item), checked=lambda item: is_model_selected("builder", "deepseek-r1:14b"), radio=True),
            item("🏛️ gemma4:26b", lambda icon, item: set_model_selection_tray("builder", "gemma4:26b")(icon, item), checked=lambda item: is_model_selected("builder", "gemma4:26b"), radio=True),
        )),
        item("Biasing Mode", Menu(
            item("Adaptive (Auto-scales with budget)", set_bias_mode("adaptive", 0.35), checked=is_bias_mode_active("adaptive"), radio=True),
            item("Manual: 50% Local Bias", set_bias_mode("manual", 0.50), checked=is_bias_mode_active("manual", 0.50), radio=True),
            item("Manual: 80% Local Bias", set_bias_mode("manual", 0.80), checked=is_bias_mode_active("manual", 0.80), radio=True),
            item("Strict 100% Local Mode", set_bias_mode("local_only", 1.0), checked=is_bias_mode_active("local_only"), radio=True),
        )),
        Menu.SEPARATOR,
        item(get_stats_text, lambda icon, item: None, enabled=False),
        item("Launch on Windows Startup", toggle_startup, checked=lambda item: is_startup_enabled()),
        Menu.SEPARATOR,
        item("Restart Server", restart_server),
        item("Exit", exit_action)
    )


def main():
    acquire_single_instance_lock()

    if not ICON_PATH.exists():
        img = Image.new("RGBA", (64, 64), (16, 185, 129, 255))
    else:
        img = Image.open(ICON_PATH)

    icon = pystray.Icon(
        name="RTX5090Cascade",
        icon=img,
        title="CascadeGateway",
        menu=create_menu()
    )

    icon.run_detached()
    start_uvicorn_server()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        with open(DATA_DIR / "tray_app.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- Crash at {time.ctime()} ---\n")
            traceback.print_exc(file=f)
        raise
