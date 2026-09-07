"""
CascadeGateway High-Speed Structural & Lexical Intent Classifier
Provides sub-5ms deterministic routing between local RTX 5090 and cloud frontier models.
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple


def validate_structure(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    hardware_info: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Phase 1: Structural & Hardware Validation (<1ms)."""
    # Check 1: Tool and Function calling schemas
    if tools and len(tools) > 0:
        return False, "Tool/function calling requires cloud schema reliability"

    # Check 2: Context length vs safe VRAM budget ceiling
    tier = hardware_info.get("tier", {})
    safe_context_tokens = tier.get("max_context", 32768)

    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total_chars += len(part["text"])

    estimated_tokens = max(1, total_chars // 4)
    if estimated_tokens > safe_context_tokens:
        return False, f"Context ({estimated_tokens} tokens) exceeds local VRAM safe ceiling ({safe_context_tokens} tokens)"

    # Check 3: Multi-turn conversation depth (>12 turns causes attention loss in sub-30B models)
    if len(messages) > 12:
        return False, f"Conversation depth ({len(messages)} turns) exceeds local attention ceiling (>12 turns)"

    return True, None


class LexicalClassifier:
    """Phase 2: Ultra-Fast Lexical Scan (1-2ms) via single-pass regex word automaton."""

    CLOUD_TRIGGERS = [
        "race condition", "deadlock", "memory leak", "thread safety", "system design",
        "architectural tradeoffs", "refactor across files", "cryptographic vulnerability",
        "exploit", "algorithmic complexity", "dynamic programming", "formal verification",
        "formal proof", "distributed consensus", "concurrency hazard", "lock contention",
        "zero-day", "microarchitectural attack", "byzantine fault", "distributed transaction"
    ]

    LOCAL_TRIGGERS = [
        "docstring", "type annotations", "write regex", "explain regex", "unit test",
        "pytest", "format json", "convert to yaml", "syntax error", "fix typo",
        "boilerplate", "getter and setter", "dataclass", "pydantic model",
        "rename variable", "format code", "add logging", "string manipulation",
        "reverse a string", "helper function", "write a test", "crud"
    ]

    def __init__(self):
        cloud_pattern = r"\b(?:" + "|".join(re.escape(t) for t in self.CLOUD_TRIGGERS) + r")\b"
        local_pattern = r"\b(?:" + "|".join(re.escape(t) for t in self.LOCAL_TRIGGERS) + r")\b"
        self.cloud_regex = re.compile(cloud_pattern, re.IGNORECASE)
        self.local_regex = re.compile(local_pattern, re.IGNORECASE)

    def classify_with_latency(self, user_text: str) -> Tuple[str, str, float, float]:
        t0 = time.perf_counter()

        cloud_match = self.cloud_regex.search(user_text)
        if cloud_match:
            matched = cloud_match.group(0)
            dur_ms = (time.perf_counter() - t0) * 1000.0
            return "cloud", f"Cloud Trigger matched: '{matched}'", 0.95, dur_ms

        local_match = self.local_regex.search(user_text)
        if local_match:
            matched = local_match.group(0)
            dur_ms = (time.perf_counter() - t0) * 1000.0
            return "local", f"Local Trigger matched: '{matched}'", 0.05, dur_ms

        dur_ms = (time.perf_counter() - t0) * 1000.0
        return "local", "Ambiguous query defaulted to local (Maximize RTX 5090 VRAM)", 0.20, dur_ms


# Pre-compile singleton classifier instance at module load
CLASSIFIER = LexicalClassifier()


def route_request(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    requested_model: str,
    hardware_info: Dict[str, Any],
    biasing_state: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Executes the full 3-phase classification pipeline in <5ms.
    Returns:
        route: "local" | "cloud"
        phase: int (0=override, 1=structural, 2=lexical)
        reason: str
        complexity_score: float
        latency_ms: float
    """
    t_start = time.perf_counter()
    req_lower = (requested_model or "").lower()

    # Explicit Model Override (Phase 0)
    if "gemini" in req_lower or "cloud" in req_lower:
        return {
            "route": "cloud",
            "phase": 0,
            "reason": f"Explicit model override: '{requested_model}'",
            "complexity_score": 1.0,
            "latency_ms": (time.perf_counter() - t_start) * 1000.0
        }
    if "local" in req_lower or "5090" in req_lower or "qwen" in req_lower:
        return {
            "route": "local",
            "phase": 0,
            "reason": f"Explicit model override: '{requested_model}'",
            "complexity_score": 0.0,
            "latency_ms": (time.perf_counter() - t_start) * 1000.0
        }

    # Phase 1: Structural & Hardware Validation (<1ms)
    is_valid_for_local, escalation_reason = validate_structure(messages, tools, hardware_info)
    if not is_valid_for_local:
        return {
            "route": "cloud",
            "phase": 1,
            "reason": escalation_reason,
            "complexity_score": 1.0,
            "latency_ms": (time.perf_counter() - t_start) * 1000.0
        }

    # Phase 2: Ultra-Fast Lexical Scan (1-2ms)
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                user_text = c
            elif isinstance(c, list):
                user_text = " ".join([p.get("text", "") for p in c if isinstance(p, dict) and "text" in p])
            break

    route, reason, complexity_score, scan_latency = CLASSIFIER.classify_with_latency(user_text)

    # Biasing Override Check
    biasing_mode = biasing_state.get("mode", "adaptive")
    if biasing_mode == "local_only":
        route = "local"
        reason += " [Strict 100% Local Mode]"
    elif biasing_mode == "manual":
        bias_factor = biasing_state.get("bias_factor", 0.35)
        # If manual bias is strong (e.g. >0.8), route local unless complexity is very high
        if complexity_score < bias_factor:
            route = "local"

    total_latency_ms = (time.perf_counter() - t_start) * 1000.0
    return {
        "route": route,
        "phase": 2,
        "reason": reason,
        "complexity_score": complexity_score,
        "latency_ms": round(total_latency_ms, 3)
    }
