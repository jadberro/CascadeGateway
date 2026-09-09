"""
CascadeGateway Intelligent Routing & Biasing Engine
Manages token metrics, dynamic biasing, model selection, workflow modes, and Ollama/Gemini clients.
"""

import json
import time
from typing import List, Dict, Any, Tuple, Optional

import httpx

from cascadegateway.core.config import config, METRICS_FILE
from cascadegateway.core.hardware import HARDWARE_INFO, match_best_model


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


def load_persistent_metrics() -> Dict[str, Any]:
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
        "name": "Solo Sprint (Builder)",
        "description": "Direct high-speed local code generation via Qwen 2.5 Coder 32B ($0 token cost).",
        "icon": "⚡",
        "has_gate": False
    },
    "builder": {
        "name": "Direct Builder (Qwen 32B)",
        "description": "Direct high-speed local code generation via Qwen 2.5 Coder 32B ($0 token cost).",
        "icon": "⚡",
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
    },
    "verify": {
        "name": "Asymmetric Verification",
        "description": "Local GPU drafts candidate code ($0 cost) -> Cloud Gemini acts as strict auditor & critic.",
        "icon": "🛡️",
        "has_gate": False
    }
}


def get_estimated_dollars_saved() -> float:
    """Calculates estimated dollar savings at $3.00 per 1M tokens saved locally."""
    total_saved = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    return round((total_saved / 1_000_000.0) * 3.00, 2)

WORKFLOW_STATE = {
    "active_mode": "architect"
}

HUD_STATE = {
    "status": "ready",
    "route": "Local GPU (Ready)",
    "model": "qwen2.5-coder:32b",
    "route_time": "0.00ms",
    "reason": "System Initialized & Warm in VRAM",
    "last_query": "Awaiting IDE prompt...",
    "updated_at": time.strftime("%H:%M:%S")
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

MODEL_SELECTION_STATE = {
    "architect": "auto",
    "builder": "auto"
}


def select_architect_model(installed_models: List[str]) -> str:
    # On single-GPU systems (<=32GB VRAM), prioritize keeping the primary 32B model resident
    # to avoid 28GB VRAM evictions and 30-second SSD reload freezes on every task switch.
    primary = config.get("local", {}).get("primary_model")
    if primary and any(primary in m for m in installed_models):
        for m in installed_models:
            if primary in m:
                return m
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


def select_best_local_model(available_models: List[str]) -> str:
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

    return match_best_model(available_models, HARDWARE_INFO["tier"])


def resolve_active_model(role: str, installed_models: List[str]) -> str:
    user_choice = MODEL_SELECTION_STATE.get(role, "auto")
    if user_choice and user_choice != "auto" and user_choice in installed_models:
        return user_choice
    if role == "architect":
        return select_architect_model(installed_models)
    return select_best_local_model(installed_models)


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


def calculate_effective_bias() -> Tuple[float, float, str]:
    mode = BIASING_STATE["mode"]
    base_threshold = config.get("cascading", {}).get("threshold", 0.35)
    cloud_tokens = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    budget = max(1000, BIASING_STATE["cloud_token_budget"])

    if mode == "local_only":
        effective_bias = 1.0
        desc = "Strict Local Mode (100% Local GPU Execution)"
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


def evaluate_routing(messages: List[Dict[str, Any]], requested_model: str) -> Tuple[str, str, float]:
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


async def call_ollama_non_streaming(base_url: str, model: str, messages: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
    timeout = config.get("local", {}).get("timeout_seconds", 120)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,  # Keep permanently resident in GPU VRAM
            "options": {"temperature": kwargs.get("temperature", 0.7), "num_ctx": 32768}
        }
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            payload["options"]["num_predict"] = kwargs["max_tokens"]

        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        msg = data.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        if not content and thinking:
            content = f"<think>\n{thinking}\n</think>\n"
        elif thinking and "<think>" not in content:
            content = f"<think>\n{thinking}\n</think>\n{content}"

        prompt_eval = data.get("prompt_eval_count", count_messages_tokens(messages))
        eval_count = data.get("eval_count", estimate_tokens(content))

        return {
            "id": f"chatcmpl-local-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": f"local:{model}",
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
