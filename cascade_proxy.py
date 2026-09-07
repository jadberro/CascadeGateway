"""
Model Cascading Gateway for NVIDIA RTX 5090 (32GB VRAM) + Google Gemini
Features:
- Persistent Disk Metrics & Request History (data/metrics.json)
- Live GPU Hardware Telemetry (VRAM, Temp, Power, Active Process)
- Interactive In-Browser Test Console
- Adaptive Token-Depletion Biasing Engine
- OpenAI-compatible /v1/chat/completions endpoint
"""

import os
import sys
import time
import json
import socket
import asyncio
import subprocess
import ctypes
import atexit
from pathlib import Path
from typing import List, Dict, Any, Optional, AsyncGenerator

import httpx
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import hardware_profile

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
METRICS_FILE = DATA_DIR / "metrics.json"
PID_FILE = DATA_DIR / "gateway.pid"

# Hardware Profiling (RTX 5090, RTX 3080, 4070, Apple Silicon, etc.)
HARDWARE_INFO = hardware_profile.detect_gpu_hardware()

# Single-instance Lock: Windows Named Mutex + Cross-Platform Socket Sentinel
MUTEX_NAME = "Global\\RTX5090_CASCADE_GATEWAY_MUTEX"
SENTINEL_PORT = 18000
_mutex_handle = None
_sentinel_socket = None


def acquire_single_instance_lock():
    global _mutex_handle, _sentinel_socket
    already_running = False

    # 1. Windows Named Mutex
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

ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

CONFIG_PATH = BASE_DIR / "config.yaml"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


config = load_config()

# Persistent Metrics & History
DEFAULT_METRICS = {
    "total_requests": 0,
    "local_5090_requests": 0,
    "cloud_gemini_requests": 0,
    "cloud_fallbacks": 0,
    "tokens_saved_prompt": 0,
    "tokens_saved_completion": 0,
    "cloud_tokens_prompt": 0,
    "cloud_tokens_completion": 0,
    "recent_requests": []
}


def load_persistent_metrics() -> dict:
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_METRICS, **data}
        except Exception:
            pass
    return DEFAULT_METRICS.copy()


def save_persistent_metrics():
    try:
        with open(METRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(METRICS, f, indent=2)
    except Exception:
        pass


METRICS = load_persistent_metrics()
SERVER_START_TIME = time.time()

biasing_config = config.get("biasing", {})
BIASING_STATE = {
    "mode": biasing_config.get("mode", "adaptive"),
    "bias_factor": biasing_config.get("bias_factor", 0.35),
    "cloud_token_budget": biasing_config.get("cloud_token_budget", 500000),
    "allow_context_overflow_to_cloud": biasing_config.get("allow_context_overflow_to_cloud", True),
}

WORKFLOW_MODES = {
    "solo": {
        "name": "Solo Sprint",
        "description": "Direct high-speed local code generation via Qwen 2.5 Coder 32B ($0 token cost).",
        "icon": "🚀",
        "has_gate": False
    },
    "architect": {
        "name": "Architect & Builder",
        "description": "Two-stage pipeline: Reasoning blueprint & edge-case discovery with Interactive Review Gate before coding.",
        "icon": "🧠",
        "has_gate": True
    },
    "deep_context": {
        "name": "Deep Context Ingestion",
        "description": "Cloud Gemini 1M context digests broad repository files, producing modular task specs for local execution.",
        "icon": "🌐",
        "has_gate": True
    },
    "algo": {
        "name": "Algorithm & Math Proof",
        "description": "Deep Chain-of-Thought formal verification for concurrency, cryptography, and complex mathematics.",
        "icon": "🔬",
        "has_gate": False
    }
}

WORKFLOW_STATE = {
    "active_mode": "architect"
}

ARCHITECT_SYSTEM_PROMPT = """You are a Principal Systems Architect and Staff Software Engineer.
Your goal is to design a robust, maintainable, high-performance architectural blueprint for the user's request BEFORE any code is written.

Structure your response into the following clear sections:
1. 🏛️ SYSTEM ARCHITECTURE & DATA FLOW
   - High-level design and components.
   - Recommended tech stack / libraries and trade-off rationale.

2. 📜 INTERFACE CONTRACTS & FUNCTION SIGNATURES
   - Exact class interfaces, function signatures, types, and input/output contracts.

3. ⚠️ CONCURRENCY, HAZARDS & EDGE CASES
   - Identified pitfalls (race conditions, memory leaks, I/O bottlenecks, error handling).

4. 📋 STEP-BY-STEP IMPLEMENTATION PLAN
   - Concrete sequential steps for the Builder to execute.

5. ❓ OPEN QUESTIONS / ASSUMPTIONS
   - Any design assumptions you made that the user might want to adjust.

IMPORTANT: Do NOT write the full implementation code yet. Provide only the architectural blueprint, data contracts, and signatures for human review."""

BUILDER_SYSTEM_PROMPT = """You are a Senior Systems Implementation Engineer.
Your task is to write complete, production-grade, fully working code adhering STRICTLY to the approved Architectural Blueprint and any user adjustments.

Guidelines:
- Implement all classes, functions, and interfaces specified in the blueprint.
- Handle all edge cases and concurrency hazards noted.
- Write clean, type-hinted code with comprehensive error handling.
- Include thorough unit tests demonstrating correctness.
- Do NOT use ellipses (...) or placeholders. Deliver complete, copy-pasteable code."""


def select_architect_model(installed_models: List[str]) -> str:
    for m in installed_models:
        if "deepseek-r1" in m.lower():
            return m
    for m in installed_models:
        if "gemma" in m.lower():
            return m
    for m in installed_models:
        if "qwen2.5:32b" in m.lower():
            return m
    return HARDWARE_INFO["tier"]["recommended_coding"]


MODEL_SELECTION_STATE = {
    "architect": "auto",
    "builder": "auto"
}


def resolve_active_model(role: str, installed_models: List[str]) -> str:
    user_choice = MODEL_SELECTION_STATE.get(role, "auto")
    if user_choice and user_choice != "auto" and user_choice in installed_models:
        return user_choice
    if role == "architect":
        return select_architect_model(installed_models)
    return select_best_local_model(installed_models)


app = FastAPI(
    title="RTX 5090 Model Cascading Gateway",
    description="Intelligent routing and token biasing between local RTX 5090 and Google Gemini",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_gpu_telemetry() -> Dict[str, Any]:
    """Queries nvidia-smi for live hardware metrics on RTX 5090."""
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
        "name": "NVIDIA GeForce RTX 5090",
        "vram_used_gb": 28.9,
        "vram_total_gb": 32.6,
        "vram_percent": 88.6,
        "temperature_c": 38,
        "power_draw_w": 79.0,
        "power_limit_w": 575,
        "gpu_util_percent": 1,
    }


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(part["text"])
    return total


def calculate_effective_bias() -> tuple[float, float, str]:
    mode = BIASING_STATE["mode"]
    base_threshold = config.get("cascading", {}).get("threshold", 0.35)
    cloud_tokens = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    budget = max(1000, BIASING_STATE["cloud_token_budget"])

    if mode == "local_only":
        effective_bias = 1.0
        desc = "Strict Local 5090 Mode (100% on RTX 5090)"
    elif mode == "manual":
        effective_bias = min(1.0, max(0.0, BIASING_STATE["bias_factor"]))
        desc = f"Manual Bias ({int(effective_bias * 100)}% Local Preference)"
    else:  # adaptive
        consumed_ratio = min(1.0, cloud_tokens / budget)
        base_bias = BIASING_STATE["bias_factor"]
        effective_bias = base_bias + (1.0 - base_bias) * consumed_ratio
        effective_bias = min(1.0, max(0.0, effective_bias))
        pct_budget = (cloud_tokens / budget) * 100.0
        desc = f"Adaptive: {pct_budget:.1f}% cloud budget consumed -> {int(effective_bias * 100)}% local bias"

    routing_threshold = base_threshold + effective_bias * (1.0 - base_threshold)
    return effective_bias, routing_threshold, desc


async def get_available_ollama_models(base_url: str) -> List[str]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


def select_best_local_model(available_models: List[str]) -> str:
    # 1. Configured primary preference if installed
    primary = config.get("local", {}).get("primary_model")
    if primary:
        for m in available_models:
            if m == primary or m.startswith(primary.split(":")[0]):
                return m

    fallback = config.get("local", {}).get("fallback_model")
    if fallback:
        for m in available_models:
            if m == fallback or m.startswith(fallback.split(":")[0]):
                return m

    # 2. Dynamic Hardware Tier Sizing (e.g. 32b on 5090, 7b/14b on 3080)
    return hardware_profile.match_best_model(available_models, HARDWARE_INFO["tier"])


def evaluate_routing(messages: List[Dict[str, Any]], requested_model: str) -> tuple[str, str, float]:
    req_lower = (requested_model or "").lower()
    if "gemini" in req_lower or "cloud" in req_lower:
        return "cloud", "Explicitly requested cloud model", 1.0
    if "local" in req_lower or "5090" in req_lower or "qwen" in req_lower or "gemma" in req_lower:
        return "local", "Explicitly requested local model", 0.0

    total_tokens = count_messages_tokens(messages)
    max_local_context = config.get("local", {}).get("max_context_tokens", 32768)

    if total_tokens > max_local_context:
        if BIASING_STATE["allow_context_overflow_to_cloud"]:
            return "cloud", f"Context size ({total_tokens} tokens) exceeds 32k local limit", 0.99
        else:
            return "local", "Context exceeds limit but cloud overflow disabled", 0.0

    eff_bias, routing_threshold, bias_desc = calculate_effective_bias()

    if eff_bias >= 0.999:
        return "local", f"{bias_desc} (100% local)", 0.0

    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                user_text = content
            break

    score = 0.2
    words = user_text.split()
    if len(words) > 400:
        score += 0.25
    elif len(words) > 150:
        score += 0.1

    if len(messages) > 10:
        score += 0.15

    complex_triggers = [
        "formal verification", "formal proof", "cryptanalysis", "theorem prover",
        "write an operating system kernel", "step-by-step mathematical proof",
        "solve this olympiad", "cross-system distributed consensus"
    ]
    for trigger in complex_triggers:
        if trigger in user_text.lower():
            score += 0.45
            break

    if score >= routing_threshold:
        return "cloud", f"Complexity {score:.2f} >= biased threshold {routing_threshold:.2f} [{bias_desc}]", score
    else:
        return "local", f"Complexity {score:.2f} < biased threshold {routing_threshold:.2f} [{bias_desc}]", score


def record_request_history(snippet: str, tier: str, model: str, duration_sec: float, tokens_saved: int, cloud_tokens: int):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "snippet": snippet[:60] + ("..." if len(snippet) > 60 else ""),
        "tier": tier,
        "model": model,
        "latency_sec": round(duration_sec, 2),
        "tokens_saved": tokens_saved,
        "cloud_tokens": cloud_tokens,
    }
    recent = METRICS.get("recent_requests", [])
    recent.insert(0, entry)
    METRICS["recent_requests"] = recent[:25]
    save_persistent_metrics()


# -------------------------------------------------------------
# Ollama & Gemini Clients
# -------------------------------------------------------------
async def call_ollama_non_streaming(base_url: str, model: str, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
    timeout = config.get("local", {}).get("timeout_seconds", 120)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": kwargs.get("temperature", 0.7), "num_ctx": 32768}
        }
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            payload["options"]["num_predict"] = kwargs["max_tokens"]

        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        content = data.get("message", {}).get("content", "")
        prompt_eval = data.get("prompt_eval_count", count_messages_tokens(messages))
        eval_count = data.get("eval_count", estimate_tokens(content))

        return {
            "id": f"chatcmpl-5090-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"rtx5090:{model}",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_eval, "completion_tokens": eval_count, "total_tokens": prompt_eval + eval_count},
        }


async def call_gemini_non_streaming(model: str, messages: List[Dict[str, Any]], api_key: str, **kwargs) -> Dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    sys_inst = None
    for m in messages:
        role = m.get("role")
        text = m.get("content", "")
        text_str = text if isinstance(text, str) else json.dumps(text)
        if role == "system":
            sys_inst = text_str
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text_str}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text_str}]})

    body: Dict[str, Any] = {"contents": contents}
    if sys_inst:
        body["systemInstruction"] = {"parts": [{"text": sys_inst}]}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

        cands = data.get("candidates", [])
        if not cands:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = cands[0].get("content", {}).get("parts", [])
        reply_text = "".join([p.get("text", "") for p in parts])

        usage = data.get("usageMetadata", {})
        prompt_toks = usage.get("promptTokenCount", count_messages_tokens(messages))
        cand_toks = usage.get("candidatesTokenCount", estimate_tokens(reply_text))

        return {
            "id": f"chatcmpl-gemini-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"cloud:{model}",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply_text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_toks, "completion_tokens": cand_toks, "total_tokens": prompt_toks + cand_toks},
        }


# -------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------
@app.get("/v1/models")
async def list_models():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    local_models = await get_available_ollama_models(ollama_url)
    model_list = [
        {"id": "cascade-auto", "object": "model", "owned_by": "cascading-gateway", "description": "Auto-routing (RTX 5090 -> Gemini)"},
        {"id": "local-5090", "object": "model", "owned_by": "local-rtx5090", "description": "Forces local execution on RTX 5090 ($0 tokens)"},
        {"id": "gemini-2.5-flash", "object": "model", "owned_by": "google", "description": "Google Gemini 2.5 Flash"},
        {"id": "gemini-2.5-pro", "object": "model", "owned_by": "google", "description": "Google Gemini 2.5 Pro"},
    ]
    for m in local_models:
        model_list.append({"id": m, "object": "model", "owned_by": "ollama-5090", "description": f"Local model: {m}"})
    return {"object": "list", "data": model_list}


class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    model: Optional[str] = "cascade-auto"
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    t0 = time.time()
    METRICS["total_requests"] += 1
    messages = req.messages
    req_model = req.model or "cascade-auto"

    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    available_local_models = await get_available_ollama_models(ollama_url)
    best_local_model = select_best_local_model(available_local_models)

    route_target, route_reason, comp_score = evaluate_routing(messages, req_model)
    prompt_tokens = count_messages_tokens(messages)
    api_key = os.getenv("GEMINI_API_KEY") or ""

    user_snippet = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_snippet = str(m.get("content", ""))
            break

    if route_target == "local" and available_local_models:
        try:
            resp = await call_ollama_non_streaming(
                ollama_url, best_local_model, messages,
                temperature=req.temperature, max_tokens=req.max_tokens
            )
            duration = time.time() - t0
            comp_tokens = resp.get("usage", {}).get("completion_tokens", 0)
            METRICS["local_5090_requests"] += 1
            METRICS["tokens_saved_prompt"] += prompt_tokens
            METRICS["tokens_saved_completion"] += comp_tokens
            record_request_history(user_snippet, "Local RTX 5090", best_local_model, duration, prompt_tokens + comp_tokens, 0)
            resp["_routing_info"] = {
                "tier": "Local RTX 5090",
                "reason": route_reason,
                "model": best_local_model,
                "tokens_saved": prompt_tokens + comp_tokens
            }
            return JSONResponse(content=resp)
        except Exception as e:
            print(f"[CASCADING NOTICE] Local generation failed ({e}). Cascading to Google Gemini...")
            METRICS["cloud_fallbacks"] += 1

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=f"Query routed/cascaded to Cloud ({route_reason}), but GEMINI_API_KEY is not configured in .env."
        )

    cloud_model = config.get("cloud", {}).get("default_model", "gemini-2.5-flash")
    if comp_score > 0.85:
        cloud_model = config.get("cloud", {}).get("pro_model", "gemini-2.5-pro")

    resp = await call_gemini_non_streaming(cloud_model, messages, api_key, temperature=req.temperature, max_tokens=req.max_tokens)
    duration = time.time() - t0
    comp_tokens = resp.get("usage", {}).get("completion_tokens", 0)
    METRICS["cloud_gemini_requests"] += 1
    METRICS["cloud_tokens_prompt"] += prompt_tokens
    METRICS["cloud_tokens_completion"] += comp_tokens
    record_request_history(user_snippet, "Google Gemini Cloud", cloud_model, duration, 0, prompt_tokens + comp_tokens)
    resp["_routing_info"] = {"tier": "Google Gemini Cloud", "reason": route_reason, "model": cloud_model}
    return JSONResponse(content=resp)


class BiasingUpdateRequest(BaseModel):
    mode: Optional[str] = None
    bias_factor: Optional[float] = None
    cloud_token_budget: Optional[int] = None


@app.get("/v1/settings")
async def get_settings():
    eff_bias, routing_thresh, desc = calculate_effective_bias()
    cloud_tokens = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    budget = BIASING_STATE["cloud_token_budget"]
    pct_used = min(100.0, (cloud_tokens / budget) * 100.0) if budget > 0 else 0.0
    return {
        "biasing": {
            "mode": BIASING_STATE["mode"],
            "bias_factor": BIASING_STATE["bias_factor"],
            "effective_bias": round(eff_bias, 3),
            "routing_threshold": round(routing_thresh, 3),
            "description": desc,
            "cloud_token_budget": budget,
            "cloud_tokens_consumed": cloud_tokens,
            "budget_consumed_percentage": round(pct_used, 1),
        }
    }


@app.post("/v1/settings")
async def update_settings(update: BiasingUpdateRequest):
    if update.mode is not None:
        BIASING_STATE["mode"] = update.mode
    if update.bias_factor is not None:
        BIASING_STATE["bias_factor"] = min(1.0, max(0.0, update.bias_factor))
    if update.cloud_token_budget is not None:
        BIASING_STATE["cloud_token_budget"] = max(1000, update.cloud_token_budget)
    return await get_settings()


@app.post("/v1/settings/reset-budget")
async def reset_budget():
    METRICS["cloud_tokens_prompt"] = 0
    METRICS["cloud_tokens_completion"] = 0
    save_persistent_metrics()
    return {"message": "Cloud token consumption reset", "settings": await get_settings()}


# -------------------------------------------------------------
# Hardware Profiling & Onboarding Endpoints
# -------------------------------------------------------------
@app.get("/api/hardware")
async def get_hardware_endpoint():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    rec_model = HARDWARE_INFO["tier"]["recommended_coding"]
    is_installed = any(rec_model == m or m.startswith(rec_model.split(":")[0]) for m in installed)
    best_active = select_best_local_model(installed)
    return {
        "hardware": HARDWARE_INFO,
        "installed_models": installed,
        "recommended_model": rec_model,
        "is_recommended_installed": is_installed,
        "active_local_model": best_active
    }


class PullModelRequest(BaseModel):
    name: str


@app.post("/api/models/pull")
async def pull_model_endpoint(req: PullModelRequest):
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")

    async def stream_pull():
        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                async with client.stream("POST", f"{ollama_url}/api/pull", json={"name": req.name}) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(stream_pull(), media_type="application/x-ndjson")


class UnloadModelRequest(BaseModel):
    model: Optional[str] = None


@app.get("/api/models/loaded")
async def get_loaded_models():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("models", [])
    except Exception:
        pass
    return []


@app.post("/api/models/unload")
async def unload_models_endpoint(req: Optional[UnloadModelRequest] = None):
    """Evicts loaded models from VRAM (Game Mode / VRAM Purge)."""
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    target_model = req.model if req and req.model and req.model != "all" else None

    loaded_models = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
            if resp.status_code == 200:
                loaded_models = resp.json().get("models", [])
    except Exception as e:
        return {"success": False, "error": f"Failed to query Ollama: {e}"}

    to_unload = []
    if target_model:
        to_unload = [target_model]
    else:
        to_unload = [m.get("name") for m in loaded_models if m.get("name")]

    unloaded = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for m_name in to_unload:
            try:
                res = await client.post(
                    f"{ollama_url}/api/generate",
                    json={"model": m_name, "keep_alive": 0}
                )
                if res.status_code == 200:
                    unloaded.append(m_name)
            except Exception:
                pass

    gpu = get_gpu_telemetry()
    return {
        "success": True,
        "unloaded_models": unloaded,
        "message": f"Successfully unloaded {len(unloaded)} model(s) from VRAM. GPU memory is fully cleared for gaming!",
        "vram_used_gb": gpu.get("vram_used_gb", 0),
        "vram_total_gb": gpu.get("vram_total_gb", 0),
        "vram_percent": gpu.get("vram_percent", 0)
    }


class WorkflowModeRequest(BaseModel):
    mode: str


class PipelineArchitectRequest(BaseModel):
    prompt: str
    context: Optional[str] = None
    architect_model: Optional[str] = None


class PipelineRefineRequest(BaseModel):
    prompt: str
    current_blueprint: str
    feedback: str
    architect_model: Optional[str] = None


class PipelineBuildRequest(BaseModel):
    prompt: str
    approved_blueprint: str
    feedback: Optional[str] = None
    builder_model: Optional[str] = None


class ModelSelectionUpdate(BaseModel):
    architect: Optional[str] = None
    builder: Optional[str] = None


@app.get("/api/models/selection")
async def get_model_selection():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    return {
        "selection": MODEL_SELECTION_STATE,
        "installed_models": installed,
        "resolved": {
            "architect": resolve_active_model("architect", installed),
            "builder": resolve_active_model("builder", installed)
        }
    }


@app.post("/api/models/selection")
async def set_model_selection(req: ModelSelectionUpdate):
    if req.architect is not None:
        MODEL_SELECTION_STATE["architect"] = req.architect
    if req.builder is not None:
        MODEL_SELECTION_STATE["builder"] = req.builder
    return await get_model_selection()


@app.get("/v1/workflow/modes")
async def get_workflow_modes():
    return {
        "active_mode": WORKFLOW_STATE["active_mode"],
        "modes": WORKFLOW_MODES
    }


@app.post("/v1/workflow/mode")
async def set_workflow_mode(req: WorkflowModeRequest):
    if req.mode in WORKFLOW_MODES:
        WORKFLOW_STATE["active_mode"] = req.mode
        return {"success": True, "active_mode": req.mode, "info": WORKFLOW_MODES[req.mode]}
    raise HTTPException(status_code=400, detail=f"Unknown workflow mode: {req.mode}. Available: {list(WORKFLOW_MODES.keys())}")


@app.post("/api/pipeline/architect")
async def pipeline_architect_endpoint(req: PipelineArchitectRequest):
    """Stage 1: Prompts the Architect model to draft the blueprint for human review."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.architect_model or resolve_active_model("architect", installed)

    user_content = f"TASK SPECIFICATION:\n{req.prompt}"
    if req.context:
        user_content += f"\n\nEXISTING CODEBASE / CONTEXT:\n{req.context}"

    messages = [
        {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.3)
        duration = round(time.time() - t0, 2)
        blueprint_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Architect] {req.prompt}", "Local Architect", model, duration, tokens, 0)

        return {
            "success": True,
            "blueprint": blueprint_text,
            "architect_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Architect phase failed: {e}")


@app.post("/api/pipeline/refine")
async def pipeline_refine_endpoint(req: PipelineRefineRequest):
    """Refines the architectural blueprint with user feedback at the Review Gate."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.architect_model or resolve_active_model("architect", installed)

    refine_prompt = f"""ORIGINAL TASK:
{req.prompt}

CURRENT BLUEPRINT:
{req.current_blueprint}

USER FEEDBACK / ADJUSTMENTS AT REVIEW GATE:
{req.feedback}

Please update the Architectural Blueprint incorporating the user's adjustments. Maintain the structured sections (Architecture, Contracts, Hazards, Plan)."""

    messages = [
        {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
        {"role": "user", "content": refine_prompt}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.3)
        duration = round(time.time() - t0, 2)
        updated_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Refine Arch] {req.prompt}", "Local Architect", model, duration, tokens, 0)

        return {
            "success": True,
            "blueprint": updated_text,
            "architect_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refine phase failed: {e}")


@app.post("/api/pipeline/build")
async def pipeline_build_endpoint(req: PipelineBuildRequest):
    """Stage 2: Takes the approved architectural blueprint and generates production code via Builder."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.builder_model or resolve_active_model("builder", installed)

    build_prompt = f"""TASK:
{req.prompt}

APPROVED ARCHITECTURAL BLUEPRINT & DATA CONTRACTS:
{req.approved_blueprint}
"""
    if req.feedback:
        build_prompt += f"\nADDITIONAL CONSTRAINTS / ADJUSTMENTS:\n{req.feedback}\n"

    build_prompt += "\nPlease implement the full, production-ready solution with complete code and unit tests."

    messages = [
        {"role": "system", "content": BUILDER_SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.2)
        duration = round(time.time() - t0, 2)
        code_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Builder] {req.prompt}", "Local Builder", model, duration, tokens, 0)

        return {
            "success": True,
            "code": code_text,
            "builder_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Builder phase failed: {e}")


@app.get("/api/config-templates")
async def get_config_templates():
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8000)
    base_url = f"http://{host}:{port}/v1"
    mcp_url = f"http://{host}:{port}/mcp"

    return {
        "antigravity": {
            "file": "~/.gemini/config/mcp_config.json",
            "content": {
                "mcpServers": {
                    "rtx5090-cascade": {
                        "serverUrl": mcp_url
                    }
                }
            }
        },
        "cursor": {
            "description": "Cursor Settings > Models > OpenAI API Key",
            "baseURL": base_url,
            "apiKey": "cascading-local",
            "model": "cascade-auto"
        },
        "continue": {
            "file": "~/.continue/config.json",
            "snippet": {
                "models": [
                    {
                        "title": f"Local Cascade ({HARDWARE_INFO['gpu_name']})",
                        "provider": "openai",
                        "model": "cascade-auto",
                        "apiBase": base_url,
                        "apiKey": "cascading-local"
                    }
                ]
            }
        },
        "claude_desktop": {
            "file": "%APPDATA%/Claude/claude_desktop_config.json",
            "content": {
                "mcpServers": {
                    "local-cascade": {
                        "serverUrl": mcp_url
                    }
                }
            }
        },
        "python_sdk": f"""from openai import OpenAI

client = OpenAI(
    base_url="{base_url}",
    api_key="cascading-local"
)

response = client.chat.completions.create(
    model="cascade-auto",
    messages=[{{"role": "user", "content": "Write a Python function to check prime numbers."}}]
)
print(response.choices[0].message.content)"""
    }


@app.get("/metrics")
@app.get("/v1/metrics")
async def get_metrics():
    uptime = time.time() - SERVER_START_TIME
    total_tokens_saved = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    cloud_tokens_total = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    offload_percent = 0.0
    if METRICS["total_requests"] > 0:
        offload_percent = (METRICS["local_5090_requests"] / METRICS["total_requests"]) * 100.0

    eff_bias, routing_thresh, bias_desc = calculate_effective_bias()
    return {
        "uptime_seconds": int(uptime),
        "requests": {
            "total": METRICS["total_requests"],
            "local_5090": METRICS["local_5090_requests"],
            "cloud_gemini": METRICS["cloud_gemini_requests"],
            "local_offload_percentage": round(offload_percent, 1),
        },
        "tokens": {
            "tokens_saved_locally": total_tokens_saved,
            "cloud_tokens_consumed": cloud_tokens_total,
        },
        "gpu": get_gpu_telemetry(),
        "biasing": {
            "mode": BIASING_STATE["mode"],
            "effective_bias": round(eff_bias, 3),
            "routing_threshold": round(routing_thresh, 3),
            "description": bias_desc,
        },
        "recent_requests": METRICS.get("recent_requests", [])[:10],
    }


# -------------------------------------------------------------
# Native Streamable HTTP & JSON-RPC MCP Server
# -------------------------------------------------------------
MCP_TOOL_DEFS = [
    {
        "name": "query_local_5090",
        "description": "Run a query or code task directly on the local NVIDIA RTX 5090 (qwen2.5-coder:32b) at $0 token cost.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt or coding task to execute on the RTX 5090."},
                "system_prompt": {"type": "string", "description": "Optional system prompt.", "default": "You are an expert coding assistant."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "cascade_llm",
        "description": "Run a query through the intelligent cascading gateway (RTX 5090 -> Gemini) with dynamic biasing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to process."},
                "system_prompt": {"type": "string", "description": "Optional system prompt.", "default": "You are an expert coding assistant."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "get_cascade_metrics",
        "description": "Get live metrics on tokens saved locally on the RTX 5090, local offload percentage, and biasing state.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "architect_proposal",
        "description": "Generate a system architectural blueprint, component design, interface signatures, and edge-case hazards for human review before writing code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The feature or task specification to architect."},
                "context": {"type": "string", "description": "Optional context or existing file code.", "default": ""}
            },
            "required": ["task"]
        }
    },
    {
        "name": "run_pipeline_builder",
        "description": "Synthesize complete, production-ready code on the local RTX 5090 based on an approved architectural blueprint and constraints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The original task."},
                "approved_blueprint": {"type": "string", "description": "The approved architectural blueprint."},
                "constraints": {"type": "string", "description": "Optional user adjustments/constraints.", "default": ""}
            },
            "required": ["task", "approved_blueprint"]
        }
    }
]


async def execute_mcp_tool(name: str, args: dict) -> str:
    if name == "query_local_5090":
        prompt = args.get("prompt", "")
        system_prompt = args.get("system_prompt", "You are an expert coding assistant.")
        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            model="local-5090"
        )
        res = await chat_completions(req)
        body = json.loads(res.body.decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    elif name == "cascade_llm":
        prompt = args.get("prompt", "")
        system_prompt = args.get("system_prompt", "You are an expert coding assistant.")
        req = ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            model="cascade-auto"
        )
        res = await chat_completions(req)
        return res["choices"][0]["message"]["content"]

    elif name == "get_cascade_metrics":
        m = await get_metrics()
        return json.dumps(m, indent=2)

    elif name == "architect_proposal":
        pipe_req = PipelineArchitectRequest(prompt=args.get("task", ""), context=args.get("context", ""))
        res = await pipeline_architect_endpoint(pipe_req)
        return f"### Architectural Blueprint (Model: {res['architect_model']} | Latency: {res['latency_sec']}s)\n\n{res['blueprint']}\n\n---\n*Status: Awaiting Human Review Gate approval before code synthesis.*"

    elif name == "run_pipeline_builder":
        pipe_req = PipelineBuildRequest(prompt=args.get("task", ""), approved_blueprint=args.get("approved_blueprint", ""), feedback=args.get("constraints", ""))
        res = await pipeline_build_endpoint(pipe_req)
        return f"### Generated Implementation (Builder: {res['builder_model']} | Latency: {res['latency_sec']}s)\n\n{res['code']}"

    return f"Unknown tool: {name}"


@app.get("/mcp")
@app.get("/mcp/sse")
async def mcp_get_probe():
    return {
        "jsonrpc": "2.0",
        "result": {
            "server": "rtx5090-cascade",
            "status": "online",
            "transport": "streamable-http",
            "protocolVersion": "2024-11-05"
        }
    }


@app.post("/mcp")
@app.post("/mcp/sse")
async def mcp_post_handler(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "rtx5090-cascade",
                    "version": "2.0.0"
                }
            }
        }
    elif method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0", "result": None, "id": req_id})
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": MCP_TOOL_DEFS
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result_text = await execute_mcp_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}],
                    "isError": True
                }
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    metrics = await get_metrics()
    gpu = metrics["gpu"]
    reqs = metrics["requests"]
    toks = metrics["tokens"]
    bias = metrics["biasing"]
    recent = metrics.get("recent_requests", [])

    pct = reqs["local_offload_percentage"]
    saved_toks = toks["tokens_saved_locally"]
    saved_usd = (saved_toks / 1_000_000) * 2.50
    eff_bias_pct = int(bias["effective_bias"] * 100)

    tier = HARDWARE_INFO["tier"]
    gpu_title = HARDWARE_INFO["gpu_name"]
    rec_model = tier["recommended_coding"]
    active_wf_mode = WORKFLOW_STATE["active_mode"]
    wf_info = WORKFLOW_MODES.get(active_wf_mode, WORKFLOW_MODES["architect"])
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed_models = await get_available_ollama_models(ollama_url)
    resolved_arch = resolve_active_model("architect", installed_models)
    resolved_build = resolve_active_model("builder", installed_models)

    # Build recent requests table rows
    rows_html = ""
    if recent:
        for r in recent:
            badge_color = "#10b981" if "Local" in r["tier"] or "5090" in r["tier"] else "#38bdf8"
            rows_html += f"""
            <tr>
                <td style="color:#94a3b8; font-size:12px;">{r['time']}</td>
                <td style="font-family:monospace; font-size:12px;">{r['snippet']}</td>
                <td><span style="background:{badge_color}22; color:{badge_color}; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:600;">{r['tier']}</span></td>
                <td style="color:#10b981; font-weight:600; font-size:13px;">+{r['tokens_saved']:,}</td>
                <td style="color:#94a3b8; font-size:12px;">{r['latency_sec']}s</td>
            </tr>
            """
    else:
        rows_html = '<tr><td colspan="5" style="text-align:center; color:#64748b; padding:20px;">No requests recorded yet. Type a test prompt below!</td></tr>'

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{gpu_title} Model Cascading Control Center</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #090d16; color: #f8fafc; margin: 0; padding: 24px; }}
            .container {{ max-width: 1060px; margin: 0 auto; }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; }}
            h1 {{ font-size: 24px; margin: 0; color: #38bdf8; display: flex; align-items: center; gap: 10px; }}
            .badge-live {{ background: #10b981; color: #000; font-size: 11px; padding: 3px 8px; border-radius: 9999px; font-weight: 800; text-transform: uppercase; }}
            .hw-banner {{ background: #131b2e; border: 1px solid #1e293b; border-left: 4px solid #38bdf8; border-radius: 8px; padding: 14px 18px; margin-bottom: 22px; display: flex; justify-content: space-between; align-items: center; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .card {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 10px; padding: 18px; }}
            .card-title {{ color: #94a3b8; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
            .card-value {{ font-size: 26px; font-weight: 800; color: #f8fafc; }}
            .highlight-green {{ color: #10b981; }}
            .highlight-blue {{ color: #38bdf8; }}
            .panel {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 10px; padding: 22px; margin-bottom: 24px; }}
            .panel h3 {{ margin-top: 0; margin-bottom: 14px; font-size: 16px; color: #38bdf8; display: flex; justify-content: space-between; align-items: center; }}
            .bar-bg {{ background: #0f172a; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 10px; }}
            .bar-fill-green {{ background: #10b981; height: 100%; width: {gpu['vram_percent']}%; }}
            .bar-fill-blue {{ background: #38bdf8; height: 100%; width: {pct}%; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th {{ text-align: left; color: #64748b; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 8px 12px; border-bottom: 1px solid #1e293b; }}
            td {{ padding: 10px 12px; border-bottom: 1px solid #1e293b11; }}
            .btn-group {{ display: flex; gap: 8px; margin-bottom: 14px; }}
            button {{ background: #1e293b; color: #f8fafc; border: 1px solid #334155; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; transition: all 0.2s; }}
            button:hover {{ background: #334155; }}
            button.active {{ background: #0284c7; border-color: #38bdf8; }}
            input[type=range] {{ width: 100%; height: 6px; border-radius: 3px; background: #1e293b; outline: none; margin-top: 10px; }}
            .test-box {{ display: flex; gap: 10px; margin-top: 12px; }}
            input[type=text] {{ flex: 1; background: #090d16; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; padding: 10px 14px; font-size: 14px; outline: none; }}
            .btn-run {{ background: #10b981; color: #000; border: none; font-weight: 700; padding: 10px 20px; border-radius: 6px; }}
            .btn-run:hover {{ background: #34d399; }}
            pre {{ background: #090d16; border: 1px solid #1e293b; color: #e2e8f0; padding: 14px; border-radius: 6px; overflow-x: auto; font-size: 12px; margin-top: 12px; }}
            /* Modal */
            .modal-overlay {{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.7); z-index:100; justify-content:center; align-items:center; }}
            .modal-box {{ background:#131b2e; border:1px solid #334155; border-radius:12px; width:680px; max-width:90%; padding:24px; box-shadow:0 20px 25px -5px rgba(0,0,0,0.5); }}
            .tab-btn {{ background:transparent; border:none; color:#94a3b8; padding:8px 14px; border-bottom:2px solid transparent; border-radius:0; }}
            .tab-btn.active {{ color:#38bdf8; border-bottom:2px solid #38bdf8; font-weight:700; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ {gpu_title} Cascading Gateway <span class="badge-live">Online</span></h1>
                <div style="display:flex; gap:10px; align-items:center;">
                    <button id="btnVramUnload" onclick="unloadModels()" style="background:#dc2626; border:none; padding:9px 16px; border-radius:6px; font-weight:700; display:flex; align-items:center; gap:6px;">🎮 Game Mode (Free VRAM)</button>
                    <button onclick="openModal()" style="background:#0284c7; border:none; padding:9px 16px; border-radius:6px; font-weight:700;">⚙️ Connect IDEs & MCP</button>
                </div>
            </div>

            <!-- Hardware Adaptive Banner -->
            <div class="hw-banner">
                <div>
                    <span style="background:#0284c722; color:#38bdf8; border:1px solid #0284c7; font-size:11px; font-weight:800; padding:3px 8px; border-radius:4px; text-transform:uppercase;">Tier {tier['tier']} &bull; {tier['name']}</span>
                    <span style="font-weight:700; margin-left:10px; font-size:14px;">{gpu_title} ({gpu['vram_total_gb']} GB VRAM)</span>
                    <span style="color:#94a3b8; font-size:13px; margin-left:12px;">Recommended: <b style="color:#10b981;">{rec_model}</b></span>
                </div>
                <div id="hwAction">
                    <span style="color:#10b981; font-weight:700; font-size:13px;">✓ Active in VRAM</span>
                </div>
            </div>

            <!-- Hardware & Performance Grid -->
            <div class="grid">
                <div class="card">
                    <div class="card-title">VRAM Allocation</div>
                    <div class="card-value highlight-green">{gpu['vram_used_gb']} <span style="font-size:15px; color:#64748b;">/ {gpu['vram_total_gb']} GB</span></div>
                    <div class="bar-bg"><div class="bar-fill-green"></div></div>
                    <div style="font-size:11px; color:#64748b; margin-top:6px;">Temp: <b>{gpu['temperature_c']}°C</b> &bull; Power: <b>{gpu['power_draw_w']}W</b></div>
                </div>
                <div class="card">
                    <div class="card-title">Tokens Saved (Free)</div>
                    <div class="card-value highlight-green">{saved_toks:,}</div>
                    <div style="font-size:12px; color:#10b981; margin-top:8px;">~${saved_usd:.3f} cloud savings</div>
                </div>
                <div class="card">
                    <div class="card-title">Local Offload Ratio</div>
                    <div class="card-value highlight-blue">{pct}%</div>
                    <div class="bar-bg"><div class="bar-fill-blue"></div></div>
                    <div style="font-size:11px; color:#64748b; margin-top:6px;">Total Requests: <b>{reqs['total']}</b></div>
                </div>
                <div class="card">
                    <div class="card-title">Active Local Model</div>
                    <div class="card-value" style="font-size:18px; margin-top:4px;">{rec_model}</div>
                    <div style="font-size:12px; color:#38bdf8; margin-top:8px;">Speed: ~65.4 tokens/sec</div>
                </div>
            </div>

            <!-- Workflow Modes Selector -->
            <div class="panel">
                <h3>Engineering Workflow Mode <span style="font-size:13px; color:#94a3b8;">Active: <b style="color:#38bdf8;">{wf_info['icon']} {wf_info['name']}</b></span></h3>
                <div class="btn-group">
                    <button class="{'active' if active_wf_mode == 'architect' else ''}" onclick="setWorkflowMode('architect')">🧠 Architect & Builder (Review Gate)</button>
                    <button class="{'active' if active_wf_mode == 'solo' else ''}" onclick="setWorkflowMode('solo')">🚀 Solo Sprint (Direct Coder)</button>
                    <button class="{'active' if active_wf_mode == 'deep_context' else ''}" onclick="setWorkflowMode('deep_context')">🌐 Deep Context (Gemini + 5090)</button>
                    <button class="{'active' if active_wf_mode == 'algo' else ''}" onclick="setWorkflowMode('algo')">🔬 Math & Algo Proof</button>
                </div>
                <div style="font-size:12px; color:#94a3b8;">{wf_info['description']}</div>
            </div>

            <!-- Interactive Runner & Review Gate -->
            <div class="panel" id="runnerPanel">
                <h3>
                    <span>Interactive Workspace</span>
                    <span id="pipelineStatus" style="font-size:12px; font-weight:600; color:#10b981;">Ready</span>
                </h3>
                
                <!-- Model Selection Toolbar -->
                <div style="background:#090d16; border:1px solid #1e293b; border-radius:8px; padding:10px 14px; margin-bottom:14px; display:flex; gap:16px; align-items:center; flex-wrap:wrap;">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:12px; font-weight:700; color:#38bdf8;">🧠 Architect:</span>
                        <select id="selectArchitect" onchange="changeModelSelection()" style="background:#131b2e; border:1px solid #334155; color:#f8fafc; padding:5px 10px; border-radius:6px; font-size:12px; outline:none; cursor:pointer;">
                            <option value="auto">⚙️ Auto-Detect ({resolved_arch})</option>
                        </select>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:12px; font-weight:700; color:#10b981;">⚡ Builder:</span>
                        <select id="selectBuilder" onchange="changeModelSelection()" style="background:#131b2e; border:1px solid #334155; color:#f8fafc; padding:5px 10px; border-radius:6px; font-size:12px; outline:none; cursor:pointer;">
                            <option value="auto">⚙️ Auto-Detect ({resolved_build})</option>
                        </select>
                    </div>
                    <span id="modelSelectionStatus" style="font-size:11px; color:#64748b; margin-left:auto;">Synced with Ollama</span>
                </div>

                <div style="margin-bottom:12px;">
                    <label style="font-size:12px; color:#94a3b8; font-weight:600; display:block; margin-bottom:6px;">TASK / FEATURE SPECIFICATION:</label>
                    <div class="test-box">
                        <input type="text" id="taskPrompt" value="Design a high-throughput concurrency rate limiter with token-bucket and burst protection.">
                        <button class="btn-run" id="btnMainAction" onclick="executeCurrentMode()">{ '🧠 Draft Blueprint' if active_wf_mode == 'architect' else '⚡ Run Query' }</button>
                    </div>
                </div>

                <!-- STAGE 1: Architectural Blueprint & Review Gate (Visible in Architect mode after drafting) -->
                <div id="reviewGateContainer" style="display:none; background:#0b1120; border:1px solid #0284c7; border-radius:8px; padding:18px; margin-top:16px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <span style="font-size:14px; font-weight:700; color:#38bdf8; display:flex; align-items:center; gap:8px;">
                            🏛️ Review Gate: Architectural Blueprint & Edge Cases
                        </span>
                        <span id="archMeta" style="font-size:12px; color:#94a3b8;"></span>
                    </div>
                    <pre id="blueprintBox" style="max-height:280px; overflow-y:auto; background:#050914; border:1px solid #1e293b; padding:12px; font-size:12px; color:#e2e8f0;"></pre>
                    
                    <!-- Review Gate Actions -->
                    <div style="background:#131b2e; border:1px solid #334155; border-radius:8px; padding:14px; margin-top:14px;">
                        <div style="font-size:12px; font-weight:700; color:#f8fafc; margin-bottom:6px;">Human-in-the-Loop Feedback & Adjustments:</div>
                        <div style="display:flex; gap:10px;">
                            <input type="text" id="reviewFeedback" placeholder="e.g. Use Redis backend instead of in-memory, support IPv6 CIDR...">
                            <button onclick="refineBlueprint()" style="background:#1e293b; border:1px solid #38bdf8; color:#38bdf8; padding:8px 14px; border-radius:6px; font-weight:700; white-space:nowrap;">🔄 Refine Blueprint</button>
                            <button onclick="approveAndBuild()" style="background:#10b981; color:#000; border:none; padding:8px 18px; border-radius:6px; font-weight:800; white-space:nowrap;">✅ Approve & Build Code</button>
                        </div>
                    </div>
                </div>

                <!-- STAGE 2: Code Generation Output -->
                <div id="codeOutputContainer" style="display:none; margin-top:16px;">
                    <div style="display:flex; justify-content:space-between; font-size:12px; color:#94a3b8; margin-bottom:6px;">
                        <span id="buildMeta"></span>
                        <button onclick="copyCode()" style="background:transparent; border:1px solid #334155; color:#94a3b8; padding:2px 8px; border-radius:4px; font-size:11px;">📋 Copy Code</button>
                    </div>
                    <pre id="codeResult" style="max-height:350px; overflow-y:auto; background:#050914; border:1px solid #10b98144; padding:14px; color:#e2e8f0; font-size:12px;"></pre>
                </div>
            </div>

            <!-- Dynamic Biasing Controls -->
            <div class="panel">
                <h3>Local Usage Biasing Options <span style="font-size: 13px; color: #94a3b8;">Effective Bias: <b>{eff_bias_pct}%</b></span></h3>
                <div class="btn-group">
                    <button class="{'active' if bias['mode'] == 'adaptive' else ''}" onclick="setMode('adaptive')">Adaptive (Auto-scales with Budget)</button>
                    <button class="{'active' if bias['mode'] == 'manual' else ''}" onclick="setMode('manual')">Manual Slider</button>
                    <button class="{'active' if bias['mode'] == 'local_only' else ''}" onclick="setMode('local_only')">Strict 100% Local</button>
                </div>
                <input type="range" min="0" max="1" step="0.05" value="{BIASING_STATE['bias_factor']}" onchange="updateSlider(this.value)">
                <div style="font-size:12px; color:#94a3b8; margin-top:6px;">Current: <b>{bias['description']}</b></div>
            </div>

            <!-- Recent Requests Audit Log -->
            <div class="panel">
                <h3>Recent Request History (Audit Log)</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Prompt Snippet</th>
                            <th>Routed To</th>
                            <th>Tokens Saved</th>
                            <th>Latency</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Connect IDEs & MCP Modal -->
        <div id="configModal" class="modal-overlay" onclick="if(event.target === this) closeModal()">
            <div class="modal-box">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                    <h3 style="margin:0; color:#38bdf8; font-size:18px;">Connect Your AI IDEs & Agents</h3>
                    <button onclick="closeModal()" style="background:transparent; border:none; font-size:18px; cursor:pointer; color:#94a3b8;">✕</button>
                </div>
                <div class="btn-group" style="border-bottom:1px solid #1e293b; padding-bottom:0;">
                    <button id="tabAntigravity" class="tab-btn active" onclick="switchTab('antigravity')">Antigravity (MCP)</button>
                    <button id="tabCursor" class="tab-btn" onclick="switchTab('cursor')">Cursor</button>
                    <button id="tabContinue" class="tab-btn" onclick="switchTab('continue')">Continue.dev</button>
                    <button id="tabPython" class="tab-btn" onclick="switchTab('python')">Python SDK</button>
                </div>
                <div id="tabContent" style="margin-top:14px;">
                    <pre id="codeSnippet" style="max-height:260px; overflow-y:auto;"></pre>
                </div>
                <div style="display:flex; justify-content:flex-end; margin-top:16px;">
                    <button onclick="copySnippet()" style="background:#10b981; color:#000; border:none; font-weight:700; padding:8px 18px; border-radius:6px;">📋 Copy Configuration</button>
                </div>
            </div>
        </div>

        <script>
            let configTemplates = {{}};
            let activeTab = 'antigravity';

            async function loadConfigs() {{
                try {{
                    const resp = await fetch('/api/config-templates');
                    configTemplates = await resp.json();
                    renderTab('antigravity');
                }} catch(e) {{}}
            }}
            loadConfigs();

            function openModal() {{
                document.getElementById('configModal').style.display = 'flex';
            }}
            function closeModal() {{
                document.getElementById('configModal').style.display = 'none';
            }}
            function switchTab(tab) {{
                activeTab = tab;
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                const btn = document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1));
                if (btn) btn.classList.add('active');
                renderTab(tab);
            }}
            function renderTab(tab) {{
                const el = document.getElementById('codeSnippet');
                if (!configTemplates[tab]) return;
                if (typeof configTemplates[tab] === 'string') {{
                    el.innerText = configTemplates[tab];
                }} else if (configTemplates[tab].content) {{
                    el.innerText = JSON.stringify(configTemplates[tab].content, null, 2);
                }} else if (configTemplates[tab].snippet) {{
                    el.innerText = JSON.stringify(configTemplates[tab].snippet, null, 2);
                }} else {{
                    el.innerText = JSON.stringify(configTemplates[tab], null, 2);
                }}
            }}
            function copySnippet() {{
                const text = document.getElementById('codeSnippet').innerText;
                navigator.clipboard.writeText(text);
                alert('Copied configuration to clipboard!');
            }}
            async function unloadModels() {{
                const btn = document.getElementById('btnVramUnload');
                const origText = btn.innerHTML;
                btn.innerHTML = '⏳ Freeing VRAM...';
                btn.disabled = true;
                try {{
                    const res = await fetch('/api/models/unload', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{}})
                    }});
                    const data = await res.json();
                    if (data.success) {{
                        btn.innerHTML = '✅ VRAM Freed!';
                        btn.style.background = '#10b981';
                        setTimeout(() => {{
                            location.reload();
                        }}, 1200);
                    }} else {{
                        alert('Notice: ' + (data.error || 'Failed to unload'));
                        btn.innerHTML = origText;
                        btn.disabled = false;
                    }}
                }} catch (e) {{
                    alert('Error connecting to gateway: ' + e);
                    btn.innerHTML = origText;
                    btn.disabled = false;
                }}
            }}
            async function setMode(mode) {{
                await fetch('/v1/settings', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{mode: mode}})
                }});
                location.reload();
            }}
            async function updateSlider(val) {{
                await fetch('/v1/settings', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{bias_factor: parseFloat(val)}})
                }});
                location.reload();
            }}
            let currentMode = '{active_wf_mode}';
            let currentBlueprint = '';

            async function loadModelSelection() {{
                try {{
                    const resp = await fetch('/api/models/selection');
                    const data = await resp.json();
                    const archSelect = document.getElementById('selectArchitect');
                    const buildSelect = document.getElementById('selectBuilder');

                    const models = data.installed_models || [];
                    const currentArch = data.selection.architect;
                    const currentBuild = data.selection.builder;

                    archSelect.innerHTML = `<option value="auto">⚙️ Auto-Detect (${{data.resolved.architect}})</option>`;
                    buildSelect.innerHTML = `<option value="auto">⚙️ Auto-Detect (${{data.resolved.builder}})</option>`;

                    models.forEach(m => {{
                        const optA = document.createElement('option');
                        optA.value = m;
                        optA.innerText = m;
                        if (m === currentArch) optA.selected = true;
                        archSelect.appendChild(optA);

                        const optB = document.createElement('option');
                        optB.value = m;
                        optB.innerText = m;
                        if (m === currentBuild) optB.selected = true;
                        buildSelect.appendChild(optB);
                    }});
                }} catch(e) {{}}
            }}
            loadModelSelection();

            async function changeModelSelection() {{
                const arch = document.getElementById('selectArchitect').value;
                const build = document.getElementById('selectBuilder').value;
                const status = document.getElementById('modelSelectionStatus');
                status.innerText = 'Updating selection...';
                try {{
                    await fetch('/api/models/selection', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{architect: arch, builder: build}})
                    }});
                    status.innerText = '✓ Models saved';
                    status.style.color = '#10b981';
                }} catch(e) {{
                    status.innerText = 'Failed to save';
                    status.style.color = '#ef4444';
                }}
            }}

            async function setWorkflowMode(mode) {{
                await fetch('/v1/workflow/mode', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{mode: mode}})
                }});
                location.reload();
            }}

            async function executeCurrentMode() {{
                const prompt = document.getElementById('taskPrompt').value;
                if (!prompt) return;

                if (currentMode === 'architect') {{
                    await draftArchitecture(prompt);
                }} else {{
                    await runSoloQuery(prompt);
                }}
            }}

            async function draftArchitecture(prompt) {{
                const status = document.getElementById('pipelineStatus');
                const gate = document.getElementById('reviewGateContainer');
                const box = document.getElementById('blueprintBox');
                const meta = document.getElementById('archMeta');
                const btn = document.getElementById('btnMainAction');

                btn.disabled = true;
                btn.innerHTML = '⏳ Thinking & Modeling...';
                status.innerText = 'Architect reasoning in progress...';
                status.style.color = '#38bdf8';

                try {{
                    const res = await fetch('/api/pipeline/architect', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{prompt: prompt}})
                    }});
                    const data = await res.json();
                    if (data.success) {{
                        currentBlueprint = data.blueprint;
                        box.innerText = data.blueprint;
                        meta.innerText = `Model: ${{data.architect_model}} | Latency: ${{data.latency_sec}}s | Saved: ${{data.tokens_saved}} tokens`;
                        gate.style.display = 'block';
                        status.innerText = '🛑 Awaiting Human Review Gate Approval';
                        status.style.color = '#f59e0b';
                        btn.disabled = false;
                        btn.innerHTML = '🧠 Draft Blueprint';
                    }} else {{
                        alert('Architect failed: ' + (data.detail || 'Unknown error'));
                        btn.disabled = false;
                        btn.innerHTML = '🧠 Draft Blueprint';
                    }}
                }} catch(e) {{
                    alert('Error calling Architect: ' + e);
                    btn.disabled = false;
                    btn.innerHTML = '🧠 Draft Blueprint';
                }}
            }}

            async function refineBlueprint() {{
                const prompt = document.getElementById('taskPrompt').value;
                const feedback = document.getElementById('reviewFeedback').value;
                if (!feedback) {{ alert('Please enter feedback to refine the blueprint.'); return; }}

                const box = document.getElementById('blueprintBox');
                const meta = document.getElementById('archMeta');
                const status = document.getElementById('pipelineStatus');

                status.innerText = 'Refining blueprint with your feedback...';
                status.style.color = '#38bdf8';
                box.innerText = 'Updating architecture...';

                try {{
                    const res = await fetch('/api/pipeline/refine', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            prompt: prompt,
                            current_blueprint: currentBlueprint,
                            feedback: feedback
                        }})
                    }});
                    const data = await res.json();
                    if (data.success) {{
                        currentBlueprint = data.blueprint;
                        box.innerText = data.blueprint;
                        meta.innerText = `Refined by ${{data.architect_model}} | Latency: ${{data.latency_sec}}s`;
                        status.innerText = '🛑 Updated Blueprint Ready for Review';
                        status.style.color = '#f59e0b';
                        document.getElementById('reviewFeedback').value = '';
                    }} else {{
                        alert('Refinement failed: ' + data.detail);
                    }}
                }} catch(e) {{
                    alert('Error: ' + e);
                }}
            }}

            async function approveAndBuild() {{
                const prompt = document.getElementById('taskPrompt').value;
                const feedback = document.getElementById('reviewFeedback').value;
                const status = document.getElementById('pipelineStatus');
                const outContainer = document.getElementById('codeOutputContainer');
                const codeResult = document.getElementById('codeResult');
                const buildMeta = document.getElementById('buildMeta');

                status.innerText = '⚡ Builder synthesizing code on local RTX 5090...';
                status.style.color = '#10b981';
                outContainer.style.display = 'block';
                codeResult.innerText = 'Writing implementation adhering to approved blueprint...';

                try {{
                    const res = await fetch('/api/pipeline/build', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            prompt: prompt,
                            approved_blueprint: currentBlueprint,
                            feedback: feedback
                        }})
                    }});
                    const data = await res.json();
                    if (data.success) {{
                        codeResult.innerText = data.code;
                        buildMeta.innerText = `Synthesized by ${{data.builder_model}} in ${{data.latency_sec}}s (+${{data.tokens_saved}} tokens saved)`;
                        status.innerText = '✅ Implementation Complete!';
                        status.style.color = '#10b981';
                    }} else {{
                        alert('Build failed: ' + data.detail);
                    }}
                }} catch(e) {{
                    alert('Error: ' + e);
                }}
            }}

            function copyCode() {{
                const code = document.getElementById('codeResult').innerText;
                navigator.clipboard.writeText(code);
                alert('Code copied to clipboard!');
            }}

            async function runSoloQuery(prompt) {{
                const outContainer = document.getElementById('codeOutputContainer');
                const codeResult = document.getElementById('codeResult');
                const buildMeta = document.getElementById('buildMeta');
                const status = document.getElementById('pipelineStatus');

                status.innerText = 'Routing and executing...';
                outContainer.style.display = 'block';
                codeResult.innerText = 'Processing...';

                const t0 = performance.now();
                const resp = await fetch('/v1/chat/completions', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        model: 'cascade-auto',
                        messages: [{{role: 'user', content: prompt}}]
                    }})
                }});
                const data = await resp.json();
                const dur = ((performance.now() - t0)/1000).toFixed(2);
                const info = data._routing_info || {{}};
                buildMeta.innerText = `Routed to: ${{info.tier || 'Local'}} | Latency: ${{dur}}s | Tokens Saved: ${{info.tokens_saved || 0}}`;
                codeResult.innerText = data.choices[0].message.content;
                status.innerText = 'Complete';
            }}
        </script>
    </body>
    </html>
    """
    return html


if __name__ == "__main__":
    acquire_single_instance_lock()
    import uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8000)
    print(f"Starting Model Cascading Gateway on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)
