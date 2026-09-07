"""
Hardware Profiler and Adaptive VRAM Tier Sizing
Detects GPU architecture, physical VRAM capacity, and recommends optimal models
for any hardware configuration (RTX 3080, 4070, 4090, 5090, Apple Silicon, or CPU).
"""

import os
import platform
import subprocess
from typing import Dict, Any, List, Optional


TIER_PROFILES = [
    {
        "tier": 1,
        "name": "Flagship (24GB+ VRAM)",
        "min_vram_gb": 22.0,
        "recommended_coding": "qwen2.5-coder:32b",
        "recommended_general": "gemma4:26b",
        "alternative_coding": ["deepseek-r1:32b", "qwen2.5-coder:14b"],
        "max_context": 32768,
        "description": "Full 32B model execution in VRAM at maximum speed (60-70+ tokens/sec)."
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
                # On Apple Silicon, unified memory is shared between CPU & GPU
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


def get_tier_for_vram(vram_gb: float) -> Dict[str, Any]:
    """Returns the matching tier profile based on VRAM capacity in gigabytes."""
    for profile in TIER_PROFILES:
        if vram_gb >= profile["min_vram_gb"]:
            return profile
    return TIER_PROFILES[-1]


def match_best_model(installed_models: List[str], tier_info: Dict[str, Any]) -> str:
    """
    Selects the best available installed model based on the detected hardware tier.
    Falls back gracefully if the primary recommended model is not installed.
    """
    rec_coding = tier_info["recommended_coding"]
    rec_general = tier_info["recommended_general"]
    alts = tier_info.get("alternative_coding", [])

    candidates = [rec_coding] + alts + [rec_general]

    # Exact match first
    for c in candidates:
        if c in installed_models:
            return c

    # Prefix match (e.g. qwen2.5-coder:14b-instruct vs qwen2.5-coder:14b)
    for c in candidates:
        base = c.split(":")[0]
        size = c.split(":")[1] if ":" in c else ""
        for m in installed_models:
            if m.startswith(base) and (not size or size in m):
                return m

    # Any coding model installed
    for m in installed_models:
        if "coder" in m.lower():
            return m

    # First installed model or fallback
    return installed_models[0] if installed_models else rec_coding
