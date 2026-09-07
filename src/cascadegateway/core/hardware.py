"""
CascadeGateway Hardware Profiler & GPU Telemetry
Detects GPU architecture, physical VRAM capacity, telemetry, and tier sizing.
"""

import platform
import subprocess
from typing import Dict, Any, List


TIER_PROFILES = [
    {
        "tier": 1,
        "name": "Flagship (24GB+ VRAM)",
        "min_vram_gb": 22.0,
        "recommended_coding": "qwen2.5-coder:32b",
        "recommended_general": "gemma4:26b",
        "alternative_coding": ["deepseek-r1:32b", "deepseek-r1:14b", "qwen2.5-coder:14b"],
        "max_context": 32768,
        "description": "Full 32B model execution in VRAM at maximum speed (60-75+ tokens/sec)."
    },
    {
        "tier": 2,
        "name": "Enthusiast (12GB - 16GB VRAM)",
        "min_vram_gb": 11.5,
        "recommended_coding": "qwen2.5-coder:14b",
        "recommended_general": "mistral-small:22b-q4",
        "alternative_coding": ["qwen2.5-coder:7b", "codestral:22b"],
        "max_context": 32768,
        "description": "Ideal for RTX 4070, 4070 Ti, 4080, and RTX 3080 12GB (45-55 tokens/sec)."
    },
    {
        "tier": 3,
        "name": "Mainstream (8GB - 10GB VRAM)",
        "min_vram_gb": 7.5,
        "recommended_coding": "qwen2.5-coder:7b",
        "recommended_general": "llama3.1:8b",
        "alternative_coding": ["deepseek-coder:6.7b", "starcoder2:7b"],
        "max_context": 16384,
        "description": "Ideal for RTX 3080 10GB, RTX 3070, RTX 4060 Ti (80-100+ tokens/sec)."
    },
    {
        "tier": 4,
        "name": "Entry / CPU (< 8GB VRAM)",
        "min_vram_gb": 0.0,
        "recommended_coding": "qwen2.5-coder:1.5b",
        "recommended_general": "llama3.2:3b",
        "alternative_coding": ["qwen2.5-coder:3b", "phi3:mini"],
        "max_context": 8192,
        "description": "Ultra-lightweight models running on low-VRAM GPUs, laptops, or CPU RAM."
    }
]


def get_tier_for_vram(vram_gb: float) -> Dict[str, Any]:
    """Returns the matching tier profile based on VRAM capacity in gigabytes."""
    for profile in TIER_PROFILES:
        if vram_gb >= profile["min_vram_gb"]:
            return profile
    return TIER_PROFILES[-1]


def detect_gpu_hardware() -> Dict[str, Any]:
    """Detects available GPU hardware, physical VRAM, and operating system."""
    os_name = platform.system()

    # 1. Try NVIDIA SMI
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0 and res.stdout.strip():
            line = res.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name = parts[0]
                total_mb = float(parts[1])
                driver = parts[2] if len(parts) > 2 else "Unknown"
                vram_gb = round(total_mb / 1024, 1)
                tier_info = get_tier_for_vram(vram_gb)
                return {
                    "detected": True,
                    "gpu_name": name,
                    "vendor": "NVIDIA",
                    "vram_gb": vram_gb,
                    "driver": driver,
                    "os": os_name,
                    "tier": tier_info
                }
    except Exception:
        pass

    # 2. Try macOS Apple Silicon Unified Memory
    if os_name == "Darwin":
        try:
            res = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                bytes_mem = int(res.stdout.strip())
                ram_gb = round(bytes_mem / (1024 ** 3), 1)
                usable_vram = round(ram_gb * 0.75, 1)
                tier_info = get_tier_for_vram(usable_vram)
                return {
                    "detected": True,
                    "gpu_name": "Apple Silicon Unified Memory",
                    "vendor": "Apple",
                    "vram_gb": usable_vram,
                    "driver": "Metal",
                    "os": os_name,
                    "tier": tier_info
                }
        except Exception:
            pass

    # 3. Fallback: CPU
    return {
        "detected": False,
        "gpu_name": "Generic CPU / Integrated Graphics",
        "vendor": "Unknown",
        "vram_gb": 4.0,
        "driver": "None",
        "os": os_name,
        "tier": TIER_PROFILES[-1]
    }


def match_best_model(installed_models: List[str], tier_info: Dict[str, Any]) -> str:
    """Selects the best available installed model based on hardware tier."""
    rec_coding = tier_info["recommended_coding"]
    rec_general = tier_info.get("recommended_general", "")
    alts = tier_info.get("alternative_coding", [])
    candidates = [rec_coding] + alts + ([rec_general] if rec_general else [])

    for c in candidates:
        if c in installed_models:
            return c

    for c in candidates:
        base = c.split(":")[0]
        for inst in installed_models:
            if inst.startswith(base):
                return inst

    if installed_models:
        return installed_models[0]
    return rec_coding


def get_gpu_telemetry() -> Dict[str, Any]:
    """Queries nvidia-smi for live hardware metrics on RTX GPUs."""
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,temperature.gpu,power.draw,power.limit,utilization.gpu",
            "--format=csv,noheader,nounits"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 7:
                vram_used = float(parts[1])
                vram_total = float(parts[2])
                return {
                    "available": True,
                    "name": parts[0],
                    "vram_used_mb": vram_used,
                    "vram_total_mb": vram_total,
                    "vram_used_gb": round(vram_used / 1024, 1),
                    "vram_total_gb": round(vram_total / 1024, 1),
                    "vram_percent": round((vram_used / max(1, vram_total)) * 100, 1),
                    "temperature_c": int(parts[3]),
                    "power_draw_w": round(float(parts[4]), 1),
                    "power_limit_w": int(float(parts[5])),
                    "gpu_util_percent": int(parts[6]),
                }
    except Exception:
        pass

    return {
        "available": False,
        "name": HARDWARE_INFO.get("gpu_name", "Local Hardware"),
        "vram_used_gb": 0.0,
        "vram_total_gb": HARDWARE_INFO.get("vram_gb", 32.0),
        "vram_percent": 0.0,
        "temperature_c": 35,
        "power_draw_w": 0.0,
        "power_limit_w": 0,
        "gpu_util_percent": 0,
    }


HARDWARE_INFO = detect_gpu_hardware()
