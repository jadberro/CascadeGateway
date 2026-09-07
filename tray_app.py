"""
Windows System Tray Desktop Controller for RTX 5090 Model Cascading
Lives silently in the notification tray next to the clock.
Allows live control of Biasing Modes, Model Selection, Startup on Boot, and Web Dashboard.
"""

import os
import sys
import time
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Fix pythonw.exe: sys.stdout and sys.stderr are None when running windowless, crashing logging
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
if sys.stdout is None:
    sys.stdout = open(DATA_DIR / "tray_stdout.log", "a", encoding="utf-8", buffering=1)
if sys.stderr is None:
    sys.stderr = open(DATA_DIR / "tray_stderr.log", "a", encoding="utf-8", buffering=1)

import json
import urllib.request

from PIL import Image
import pystray
from pystray import MenuItem as item, Menu
import uvicorn

import cascade_proxy

STARTUP_DIR = Path(os.getenv("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
STARTUP_LNK = STARTUP_DIR / "RTX5090Cascade.lnk"
ICON_PATH = BASE_DIR / "assets" / "icon.png"

# Server Thread Reference
server_instance: Optional[uvicorn.Server] = None
server_thread: Optional[threading.Thread] = None


def is_port_in_use(port: int = 8000) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_uvicorn_server():
    global server_instance
    host = cascade_proxy.config.get("server", {}).get("host", "127.0.0.1")
    port = cascade_proxy.config.get("server", {}).get("port", 8000)

    uvicorn_config = uvicorn.Config(
        app=cascade_proxy.app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,
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
            icon.notify("Windows Startup: Disabled", "RTX 5090 Cascading Gateway")
        else:
            # Create shortcut using PowerShell
            ps_script = BASE_DIR / "create_shortcuts.ps1"
            if ps_script.exists():
                os.system(f'powershell.exe -ExecutionPolicy Bypass -File "{ps_script}"')
            icon.notify("Windows Startup: Enabled (Launches at boot)", "RTX 5090 Cascading Gateway")
    except Exception as e:
        icon.notify(f"Error toggling startup: {e}", "RTX 5090 Cascading Gateway")


def open_dashboard(icon, item):
    webbrowser.open("http://127.0.0.1:8000/")


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


def set_bias_mode(mode: str, factor: float = 0.35):
    def action(icon, item):
        cascade_proxy.BIASING_STATE["mode"] = mode
        cascade_proxy.BIASING_STATE["bias_factor"] = factor
        mode_names = {
            "adaptive": "Adaptive Mode (Auto-scales with budget)",
            "manual": f"Manual Mode ({int(factor*100)}% Local 5090 Bias)",
            "local_only": "Strict 100% Local 5090 Mode ($0 tokens)",
        }
        icon.notify(f"Switched to {mode_names.get(mode, mode)}", "RTX 5090 Cascading")
    return action


def is_bias_mode_active(mode: str, factor: float = None):
    def check(item):
        if cascade_proxy.BIASING_STATE["mode"] != mode:
            return False
        if factor is not None and abs(cascade_proxy.BIASING_STATE["bias_factor"] - factor) > 0.05:
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
        return cascade_proxy.WORKFLOW_STATE.get("active_mode") == mode
    return check


def set_primary_model(model_name: str):
    def action(icon, item):
        cascade_proxy.config.setdefault("local", {})["primary_model"] = model_name
        icon.notify(f"Primary Local Model set to: {model_name}", "RTX 5090 Cascading")
    return action


def is_model_active(model_name: str):
    def check(item):
        return cascade_proxy.config.get("local", {}).get("primary_model", "") == model_name
    return check


def set_model_selection_tray(role: str, model_name: str):
    def action(icon, item):
        cascade_proxy.MODEL_SELECTION_STATE[role] = model_name
        icon.notify(f"{role.capitalize()} Model set to: {model_name}", "CascadeGateway")
    return action


def is_model_selected(role: str, model_name: str) -> bool:
    return cascade_proxy.MODEL_SELECTION_STATE.get(role, "auto") == model_name


def get_stats_text(item):
    saved = cascade_proxy.METRICS["tokens_saved_prompt"] + cascade_proxy.METRICS["tokens_saved_completion"]
    reqs = cascade_proxy.METRICS["total_requests"]
    offload = 0.0
    if reqs > 0:
        offload = (cascade_proxy.METRICS["local_5090_requests"] / reqs) * 100.0
    return f"Stats: {saved:,} tokens saved ({offload:.0f}% offload)"


def restart_server(icon, item):
    global server_instance
    if server_instance:
        server_instance.should_exit = True
    time.sleep(1.0)
    launch_server_thread()
    icon.notify("Gateway server restarted on port 8000.", "RTX 5090 Cascading")


def exit_action(icon, item):
    global server_instance
    if server_instance:
        server_instance.should_exit = True
    icon.stop()
    sys.exit(0)


def create_menu():
    return Menu(
        item("RTX 5090 Model Cascading (Port 8000)", lambda icon, item: None, enabled=False),
        item("Open Web Dashboard", open_dashboard, default=True),
        item("🎮 Game Mode (Free VRAM)", free_vram_action),
        Menu.SEPARATOR,
        item("Workflow Mode", Menu(
            item("🧠 Architect & Builder (Review Gate)", set_workflow_mode_action("architect"), checked=is_workflow_mode_active("architect"), radio=True),
            item("🚀 Solo Sprint (Direct Fast Coder)", set_workflow_mode_action("solo"), checked=is_workflow_mode_active("solo"), radio=True),
            item("🌐 Deep Context (Gemini + 5090)", set_workflow_mode_action("deep_context"), checked=is_workflow_mode_active("deep_context"), radio=True),
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
            item("Strict 100% Local (5090 Only)", set_bias_mode("local_only", 1.0), checked=is_bias_mode_active("local_only"), radio=True),
        )),
        Menu.SEPARATOR,
        item(get_stats_text, lambda icon, item: None, enabled=False),
        item("Launch on Windows Startup", toggle_startup, checked=lambda item: is_startup_enabled()),
        Menu.SEPARATOR,
        item("Restart Server", restart_server),
        item("Exit", exit_action)
    )


def main():
    cascade_proxy.acquire_single_instance_lock()

    if not ICON_PATH.exists():
        # Fallback dynamic icon
        img = Image.new("RGBA", (64, 64), (16, 185, 129, 255))
    else:
        img = Image.open(ICON_PATH)

    icon = pystray.Icon(
        name="RTX5090Cascade",
        icon=img,
        title="RTX 5090 Model Cascading Gateway",
        menu=create_menu()
    )

    # Run tray icon in detached thread so main thread drives the server
    icon.run_detached()

    # Run Uvicorn server directly on main thread
    start_uvicorn_server()


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        with open(BASE_DIR / "tray_app.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- Crash at {time.ctime()} ---\n")
            traceback.print_exc(file=f)
        raise
