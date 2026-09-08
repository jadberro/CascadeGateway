"""
CascadeGateway Hardware Profiler & GPU Telemetry
Detects GPU architecture, physical VRAM capacity, telemetry, and tier sizing.
"""

import os
import ctypes
import platform
import subprocess
from typing import Dict, Any, List, Optional


def _run_silent_cmd(cmd: List[str], timeout: float = 2.0) -> Optional[subprocess.CompletedProcess]:
    """Runs a subprocess guaranteed to never spawn, flicker, or show a console window on Windows."""
    kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # subprocess.CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    try:
        return subprocess.run(cmd, **kwargs)
    except Exception:
        return None


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


# Direct in-memory Ctypes NVML handle (Zero subprocesses, zero console windows, sub-millisecond)
_NVML_LIB = None
_NVML_DEVICE = None
_NVML_INIT_ATTEMPTED = False


class _nvmlMemory_t(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class _nvmlUtilization_t(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


def _init_nvml_handle() -> bool:
    global _NVML_LIB, _NVML_DEVICE, _NVML_INIT_ATTEMPTED
    if _NVML_INIT_ATTEMPTED:
        return _NVML_LIB is not None and _NVML_DEVICE is not None
    _NVML_INIT_ATTEMPTED = True
    try:
        if os.name == "nt":
            _NVML_LIB = ctypes.CDLL("nvml.dll")
        else:
            _NVML_LIB = ctypes.CDLL("libnvidia-ml.so.1")
        _NVML_LIB.nvmlInit_v2()
        dev = ctypes.c_void_p()
        _NVML_LIB.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        _NVML_DEVICE = dev
        return True
    except Exception:
        _NVML_LIB = None
        _NVML_DEVICE = None
        return False


def _get_telemetry_nvml() -> Optional[Dict[str, Any]]:
    if not _init_nvml_handle():
        return None
    try:
        # GPU Name
        name_buf = ctypes.create_string_buffer(64)
        _NVML_LIB.nvmlDeviceGetName(_NVML_DEVICE, name_buf, 64)
        gpu_name = name_buf.value.decode("utf-8", errors="replace")

        # Memory
        mem = _nvmlMemory_t()
        _NVML_LIB.nvmlDeviceGetMemoryInfo(_NVML_DEVICE, ctypes.byref(mem))
        vram_used_mb = mem.used / (1024 * 1024)
        vram_total_mb = mem.total / (1024 * 1024)
        vram_used_gb = round(vram_used_mb / 1024, 1)
        vram_total_gb = round(vram_total_mb / 1024, 1)
        vram_percent = round((mem.used / max(1, mem.total)) * 100.0, 1)

        # Temperature (NVML_TEMPERATURE_GPU = 0)
        temp = ctypes.c_uint()
        _NVML_LIB.nvmlDeviceGetTemperature(_NVML_DEVICE, 0, ctypes.byref(temp))

        # Power Draw (milliwatts -> Watts)
        power = ctypes.c_uint()
        _NVML_LIB.nvmlDeviceGetPowerUsage(_NVML_DEVICE, ctypes.byref(power))
        power_w = round(power.value / 1000.0, 1)

        # Power Limit
        power_lim = ctypes.c_uint()
        try:
            _NVML_LIB.nvmlDeviceGetEnforcedPowerLimit(_NVML_DEVICE, ctypes.byref(power_lim))
            power_limit_w = int(power_lim.value / 1000.0)
        except Exception:
            power_limit_w = 0

        # Utilization
        util = _nvmlUtilization_t()
        _NVML_LIB.nvmlDeviceGetUtilizationRates(_NVML_DEVICE, ctypes.byref(util))

        return {
            "available": True,
            "name": gpu_name,
            "vram_used_mb": vram_used_mb,
            "vram_total_mb": vram_total_mb,
            "vram_used_gb": vram_used_gb,
            "vram_total_gb": vram_total_gb,
            "vram_percent": vram_percent,
            "temperature_c": int(temp.value),
            "power_draw_w": power_w,
            "power_limit_w": power_limit_w,
            "gpu_util_percent": int(util.gpu),
        }
    except Exception:
        return None


def detect_gpu_hardware() -> Dict[str, Any]:
    """Detects available GPU hardware, physical VRAM, and operating system."""
    os_name = platform.system()

    # 1. Try In-Memory NVML Ctypes (Instant <0.1ms, zero subprocesses, zero windows)
    try:
        if _init_nvml_handle():
            name_buf = ctypes.create_string_buffer(64)
            _NVML_LIB.nvmlDeviceGetName(_NVML_DEVICE, name_buf, 64)
            name = name_buf.value.decode("utf-8", errors="replace")
            mem = _nvmlMemory_t()
            _NVML_LIB.nvmlDeviceGetMemoryInfo(_NVML_DEVICE, ctypes.byref(mem))
            vram_gb = round(mem.total / (1024 ** 3), 1)
            tier_info = get_tier_for_vram(vram_gb)
            return {
                "detected": True,
                "gpu_name": name,
                "vendor": "NVIDIA",
                "vram_gb": vram_gb,
                "driver": "NVML Direct",
                "os": os_name,
                "tier": tier_info
            }
    except Exception:
        pass

    # 2. Fallback: Silent NVIDIA SMI via subprocess (Guaranteed no window on Windows)
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits"
        ]
        res = _run_silent_cmd(cmd, timeout=2.0)
        if res and res.returncode == 0 and res.stdout.strip():
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

    # 3. Try macOS Apple Silicon Unified Memory
    if os_name == "Darwin":
        try:
            res = _run_silent_cmd(["sysctl", "-n", "hw.memsize"], timeout=2.0)
            if res and res.returncode == 0:
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

    # 4. Fallback: CPU
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
    """Queries live hardware metrics on RTX GPUs. Uses in-process ctypes NVML first (<0.1ms, zero windows)."""
    # 1. Primary: Ultra-fast in-memory NVML ctypes (zero subprocesses, zero windows)
    nvml_data = _get_telemetry_nvml()
    if nvml_data is not None:
        return nvml_data

    # 2. Fallback: Silent nvidia-smi with CREATE_NO_WINDOW
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,temperature.gpu,power.draw,power.limit,utilization.gpu",
            "--format=csv,noheader,nounits"
        ]
        res = _run_silent_cmd(cmd, timeout=1.5)
        if res and res.returncode == 0 and res.stdout.strip():
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
        "gpu_util_percent": 0,
    }


HARDWARE_INFO = detect_gpu_hardware()
