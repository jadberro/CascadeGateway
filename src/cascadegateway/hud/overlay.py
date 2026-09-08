"""
CascadeGateway Always-On-Top Desktop HUD & Telemetry Overlay
Engineered with native tkinter for zero-dependency, ultra-lightweight execution on Windows.
"""

import sys
import time
import json
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
import urllib.request
import urllib.error

API_BASE = "http://127.0.0.1:8000"


class CascadeGatewayHUD:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CascadeGateway HUD")
        self.root.geometry("390x320+40+40")
        self.root.configure(bg="#0b0f19")
        self.root.wm_attributes("-topmost", True)
        self.root.overrideredirect(True)  # Frameless minimalist window

        # Window Drag State
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.is_pinned = True
        self.is_compact = False
        self.full_height = 330
        self.compact_height = 56

        self.data_queue = queue.Queue()
        self.stop_event = threading.Event()

        self.setup_styles()
        self.build_ui()

        # Start background polling thread
        self.poll_thread = threading.Thread(target=self.poll_gateway_worker, daemon=True)
        self.poll_thread.start()

        # Start UI event pump
        self.root.after(200, self.process_queue_updates)

    def setup_styles(self):
        self.font_title = ("Segoe UI", 10, "bold")
        self.font_pill = ("Segoe UI", 9, "bold")
        self.font_mono = ("Consolas", 9)
        self.font_small = ("Segoe UI", 8)
        self.font_btn = ("Segoe UI", 8, "bold")

    def build_ui(self):
        # Outer Border Container
        self.border_frame = tk.Frame(self.root, bg="#1e293b", padx=1, pady=1)
        self.border_frame.pack(fill=tk.BOTH, expand=True)

        self.main_container = tk.Frame(self.border_frame, bg="#0b0f19")
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # 1. Header & Titlebar (Draggable)
        self.header_frame = tk.Frame(self.main_container, bg="#0b0f19", height=40, cursor="fleur")
        self.header_frame.pack(fill=tk.X, padx=8, pady=(6, 4))
        self.header_frame.bind("<Button-1>", self.on_drag_start)
        self.header_frame.bind("<B1-Motion>", self.on_drag_motion)

        # Status Pill (Colored Indicator)
        self.status_pill = tk.Label(
            self.header_frame,
            text="● CONNECTING",
            bg="#334155",
            fg="#f8fafc",
            font=self.font_pill,
            padx=8,
            pady=2,
            bd=0
        )
        self.status_pill.pack(side=tk.LEFT)
        self.status_pill.bind("<Button-1>", self.on_drag_start)
        self.status_pill.bind("<B1-Motion>", self.on_drag_motion)

        # Window Controls (Close, Compact, Pin)
        self.btn_close = tk.Button(
            self.header_frame, text="✕", bg="#0b0f19", fg="#94a3b8",
            activebackground="#ef4444", activeforeground="#ffffff",
            bd=0, font=self.font_btn, width=2, command=self.close_window, cursor="hand2"
        )
        self.btn_close.pack(side=tk.RIGHT, padx=(2, 0))

        self.btn_compact = tk.Button(
            self.header_frame, text="▼", bg="#0b0f19", fg="#94a3b8",
            activebackground="#1e293b", activeforeground="#ffffff",
            bd=0, font=self.font_btn, width=2, command=self.toggle_compact, cursor="hand2"
        )
        self.btn_compact.pack(side=tk.RIGHT, padx=2)

        self.btn_pin = tk.Button(
            self.header_frame, text="📌", bg="#0b0f19", fg="#38bdf8",
            activebackground="#1e293b", activeforeground="#ffffff",
            bd=0, font=self.font_btn, width=2, command=self.toggle_pin, cursor="hand2"
        )
        self.btn_pin.pack(side=tk.RIGHT, padx=2)

        self.title_label = tk.Label(
            self.header_frame, text="CascadeGateway",
            bg="#0b0f19", fg="#e2e8f0", font=self.font_title
        )
        self.title_label.pack(side=tk.LEFT, padx=6)
        self.title_label.bind("<Button-1>", self.on_drag_start)
        self.title_label.bind("<B1-Motion>", self.on_drag_motion)

        # 2. Body Area (Collapsible)
        self.body_frame = tk.Frame(self.main_container, bg="#0b0f19")
        self.body_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # Model & Decision Card
        self.card_decision = tk.Frame(self.body_frame, bg="#131b2e", padx=8, pady=6, highlightbackground="#1e293b", highlightthickness=1)
        self.card_decision.pack(fill=tk.X, pady=(0, 6))

        self.lbl_model_title = tk.Label(self.card_decision, text="ACTIVE MODEL & ROUTE", bg="#131b2e", fg="#64748b", font=self.font_small)
        self.lbl_model_title.pack(anchor="w")

        self.lbl_model_val = tk.Label(self.card_decision, text="qwen2.5-coder:32b", bg="#131b2e", fg="#38bdf8", font=self.font_title)
        self.lbl_model_val.pack(anchor="w")

        self.lbl_reason = tk.Label(
            self.card_decision,
            text="Waiting for incoming IDE completion...",
            bg="#131b2e", fg="#94a3b8", font=self.font_mono,
            wraplength=350, justify="left"
        )
        self.lbl_reason.pack(anchor="w", pady=(2, 0))

        # Last Query Snippet Card
        self.card_query = tk.Frame(self.body_frame, bg="#131b2e", padx=8, pady=5, highlightbackground="#1e293b", highlightthickness=1)
        self.card_query.pack(fill=tk.X, pady=(0, 6))

        self.lbl_query_title = tk.Label(self.card_query, text="LATEST IDE QUERY", bg="#131b2e", fg="#64748b", font=self.font_small)
        self.lbl_query_title.pack(anchor="w")

        self.lbl_query_text = tk.Label(
            self.card_query, text="None yet",
            bg="#131b2e", fg="#cbd5e1", font=self.font_mono,
            wraplength=350, justify="left"
        )
        self.lbl_query_text.pack(anchor="w")

        # VRAM Meter Card
        self.card_vram = tk.Frame(self.body_frame, bg="#131b2e", padx=8, pady=6, highlightbackground="#1e293b", highlightthickness=1)
        self.card_vram.pack(fill=tk.X, pady=(0, 6))

        self.vram_header_frame = tk.Frame(self.card_vram, bg="#131b2e")
        self.vram_header_frame.pack(fill=tk.X)

        self.lbl_vram_title = tk.Label(self.vram_header_frame, text="RTX 5090 VRAM", bg="#131b2e", fg="#64748b", font=self.font_small)
        self.lbl_vram_title.pack(side=tk.LEFT)

        self.lbl_vram_stats = tk.Label(self.vram_header_frame, text="0.0 / 31.8 GB (0%)", bg="#131b2e", fg="#10b981", font=self.font_small)
        self.lbl_vram_stats.pack(side=tk.RIGHT)

        # Custom VRAM Canvas Bar
        self.canvas_vram = tk.Canvas(self.card_vram, height=8, bg="#1e293b", highlightthickness=0)
        self.canvas_vram.pack(fill=tk.X, pady=(4, 2))

        # Bottom Hardware & Savings Bar
        self.hw_stats_frame = tk.Frame(self.card_vram, bg="#131b2e")
        self.hw_stats_frame.pack(fill=tk.X)

        self.lbl_temp_power = tk.Label(self.hw_stats_frame, text="Temp: 0°C | Power: 0W", bg="#131b2e", fg="#64748b", font=self.font_small)
        self.lbl_temp_power.pack(side=tk.LEFT)

        self.lbl_savings = tk.Label(self.hw_stats_frame, text="Saved Today: $0.00", bg="#131b2e", fg="#38bdf8", font=self.font_small)
        self.lbl_savings.pack(side=tk.RIGHT)

        # Quick Control Buttons
        self.btn_frame = tk.Frame(self.body_frame, bg="#0b0f19")
        self.btn_frame.pack(fill=tk.X, pady=(2, 0))

        self.btn_architect = tk.Button(
            self.btn_frame, text="🧠 Architect", bg="#1e293b", fg="#a855f7",
            activebackground="#a855f7", activeforeground="#ffffff",
            bd=0, font=self.font_btn, pady=4, cursor="hand2",
            command=lambda: self.switch_mode("architect")
        )
        self.btn_architect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.btn_builder = tk.Button(
            self.btn_frame, text="⚡ Builder", bg="#1e293b", fg="#38bdf8",
            activebackground="#38bdf8", activeforeground="#ffffff",
            bd=0, font=self.font_btn, pady=4, cursor="hand2",
            command=lambda: self.switch_mode("builder")
        )
        self.btn_builder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_verify = tk.Button(
            self.btn_frame, text="🛡️ Verify", bg="#1e293b", fg="#10b981",
            activebackground="#10b981", activeforeground="#ffffff",
            bd=0, font=self.font_btn, pady=4, cursor="hand2",
            command=lambda: self.switch_mode("verify")
        )
        self.btn_verify.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_warm = tk.Button(
            self.btn_frame, text="▶️ Warm", bg="#1e293b", fg="#e2e8f0",
            activebackground="#4f46e5", activeforeground="#ffffff",
            bd=0, font=self.font_btn, pady=4, cursor="hand2",
            command=self.warm_gpu
        )
        self.btn_warm.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.btn_pause = tk.Button(
            self.btn_frame, text="🛑 Pause", bg="#1e293b", fg="#ef4444",
            activebackground="#dc2626", activeforeground="#ffffff",
            bd=0, font=self.font_btn, pady=4, cursor="hand2",
            command=self.pause_gpu
        )
        self.btn_pause.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

    def on_drag_start(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def on_drag_motion(self, event):
        x = self.root.winfo_x() + (event.x - self.drag_start_x)
        y = self.root.winfo_y() + (event.y - self.drag_start_y)
        self.root.geometry(f"+{x}+{y}")

    def toggle_pin(self):
        self.is_pinned = not self.is_pinned
        self.root.wm_attributes("-topmost", self.is_pinned)
        self.btn_pin.config(fg="#38bdf8" if self.is_pinned else "#64748b")

    def toggle_compact(self):
        self.is_compact = not self.is_compact
        if self.is_compact:
            self.body_frame.pack_forget()
            self.root.geometry(f"390x{self.compact_height}")
            self.btn_compact.config(text="▲")
        else:
            self.body_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
            self.root.geometry(f"390x{self.full_height}")
            self.btn_compact.config(text="▼")

    def close_window(self):
        self.stop_event.set()
        self.root.destroy()
        sys.exit(0)

    def switch_mode(self, mode: str):
        def worker():
            try:
                req = urllib.request.Request(
                    f"{API_BASE}/v1/workflow/mode",
                    data=json.dumps({"mode": mode}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    pass
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def warm_gpu(self):
        self.lbl_reason.config(text="Warming up GPU and pinning weights into VRAM...")
        def worker():
            try:
                req = urllib.request.Request(
                    f"{API_BASE}/api/models/preload",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=60.0) as resp:
                    pass
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def pause_gpu(self):
        self.lbl_reason.config(text="Unloading VRAM to 0MB for games/3D rendering...")
        def worker():
            try:
                req = urllib.request.Request(
                    f"{API_BASE}/api/models/unload",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    pass
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def poll_gateway_worker(self):
        while not self.stop_event.is_set():
            try:
                req = urllib.request.Request(f"{API_BASE}/api/hud/state")
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    data = json.loads(resp.read().decode())
                    self.data_queue.put(("data", data))
            except urllib.error.URLError:
                self.data_queue.put(("offline", None))
            except Exception:
                self.data_queue.put(("offline", None))
            time.sleep(1.0)

    def process_queue_updates(self):
        try:
            while not self.data_queue.empty():
                kind, payload = self.data_queue.get_nowait()
                if kind == "offline":
                    self.status_pill.config(text="● OFFLINE", bg="#475569", fg="#f8fafc")
                    self.lbl_reason.config(text="CascadeGateway port 8000 unreachable")
                elif kind == "data":
                    self.apply_telemetry_update(payload)
        except queue.Empty:
            pass

        self.root.after(300, self.process_queue_updates)

    def apply_telemetry_update(self, data: dict):
        route = data.get("route", "Local")
        model = data.get("model", "qwen2.5-coder:32b")
        status = data.get("status", "ready")
        reason = data.get("reason", "Ready")
        last_query = data.get("last_query", "None yet")
        vram = data.get("vram", {})
        savings = data.get("savings", {})
        workflow = data.get("workflow", {})

        # Color-coded Status Pill
        if "architect" in route.lower():
            self.status_pill.config(text="● ARCHITECT (R1)", bg="#a855f7", fg="#ffffff")
            self.lbl_model_val.config(text=f"{model} [Local CoT]", fg="#c084fc")
        elif "verify" in route.lower() or "consensus" in route.lower():
            self.status_pill.config(text="● ASYMMETRIC VERIFY", bg="#0284c7", fg="#ffffff")
            self.lbl_model_val.config(text=f"{model} [5090->Cloud]", fg="#38bdf8")
        elif "cloud" in route.lower() or "gemini" in route.lower():
            self.status_pill.config(text="● GEMINI CLOUD", bg="#d97706", fg="#ffffff")
            self.lbl_model_val.config(text=f"{model} [Cloud Fallback]", fg="#f59e0b")
        else:
            self.status_pill.config(text="● LOCAL 5090", bg="#059669", fg="#ffffff")
            self.lbl_model_val.config(text=f"{model} [Builder]", fg="#34d399")

        # Routing Reason & Latency
        route_time = data.get("route_time", "0ms")
        self.lbl_reason.config(text=f"[{route_time}] {reason}")

        # Query Snippet
        if last_query:
            clean_q = last_query.strip().replace("\n", " ")
            if len(clean_q) > 90:
                clean_q = clean_q[:87] + "..."
            self.lbl_query_text.config(text=clean_q)

        # VRAM Meter
        used = vram.get("used", 0.0)
        total = vram.get("total", 31.8)
        pct = vram.get("percent", 0)
        self.lbl_vram_stats.config(text=f"{used:.1f} / {total:.1f} GB ({pct}%)")

        # Render VRAM Bar on Canvas
        self.canvas_vram.delete("all")
        bar_w = self.canvas_vram.winfo_width()
        if bar_w < 10:
            bar_w = 360
        fill_w = max(2, int(bar_w * (pct / 100.0)))
        bar_color = "#ef4444" if pct > 95 else "#a855f7" if "architect" in route.lower() else "#10b981"
        self.canvas_vram.create_rectangle(0, 0, bar_w, 8, fill="#1e293b", width=0)
        self.canvas_vram.create_rectangle(0, 0, fill_w, 8, fill=bar_color, width=0)

        # Temp & Power
        temp = vram.get("temp", 0)
        power = vram.get("power", 0)
        self.lbl_temp_power.config(text=f"Temp: {temp}°C | Power: {power:.0f}W")

        # Savings
        dollars = savings.get("dollars", 0.0)
        offload = data.get("metrics", {}).get("offload_percent", 100.0)
        self.lbl_savings.config(text=f"Saved: ${dollars:.2f} ({offload:.0f}% Local)")

        # Highlight Active Mode Button
        active_mode = workflow.get("active_mode", "builder")
        self.btn_architect.config(bg="#a855f7" if active_mode == "architect" else "#1e293b", fg="#ffffff" if active_mode == "architect" else "#a855f7")
        self.btn_builder.config(bg="#38bdf8" if active_mode == "builder" else "#1e293b", fg="#ffffff" if active_mode == "builder" else "#38bdf8")
        self.btn_verify.config(bg="#10b981" if active_mode == "verify" else "#1e293b", fg="#ffffff" if active_mode == "verify" else "#10b981")


def main():
    root = tk.Tk()
    app = CascadeGatewayHUD(root)
    root.mainloop()


if __name__ == "__main__":
    main()
