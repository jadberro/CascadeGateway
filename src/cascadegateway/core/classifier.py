"""
CascadeGateway High-Speed Structural & Lexical Intent Classifier
Provides sub-5ms deterministic routing between local RTX 5090 and cloud frontier models.
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple


def validate_structure(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    hardware_info: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str]]:
    """Phase 1: Structural & Hardware Validation (<1ms)."""
    if hardware_info is None:
        from cascadegateway.core.hardware import HARDWARE_INFO
        hardware_info = HARDWARE_INFO

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

    ARCHITECT_TRIGGERS = [
        "race condition", "deadlock", "memory leak", "thread safety", "system design",
        "architectural tradeoffs", "refactor across files", "cryptographic vulnerability",
        "exploit", "algorithmic complexity", "dynamic programming", "formal verification",
        "formal proof", "distributed consensus", "concurrency hazard", "lock contention",
        "zero-day", "microarchitectural attack", "byzantine fault", "distributed transaction",
        "system architecture", "tradeoff analysis", "design pattern", "milestone roadmap",
        "milestone review", "architectural review", "architectural standpoint"
    ]

    LOCAL_TRIGGERS = [
        "docstring", "type annotations", "write regex", "explain regex", "unit test",
        "pytest", "format json", "convert to yaml", "syntax error", "fix typo",
        "boilerplate", "getter and setter", "dataclass", "pydantic model",
        "rename variable", "format code", "add logging", "string manipulation",
        "reverse a string", "helper function", "write a test", "crud", "implement function"
    ]

    def __init__(self):
        architect_pattern = r"\b(?:" + "|".join(re.escape(t) for t in self.ARCHITECT_TRIGGERS) + r")\b"
        local_pattern = r"\b(?:" + "|".join(re.escape(t) for t in self.LOCAL_TRIGGERS) + r")\b"
        self.architect_regex = re.compile(architect_pattern, re.IGNORECASE)
        self.local_regex = re.compile(local_pattern, re.IGNORECASE)

    def classify_with_latency(self, user_text: str) -> Tuple[str, str, float, float]:
        t0 = time.perf_counter()

        architect_match = self.architect_regex.search(user_text)
        if architect_match:
            matched = architect_match.group(0)
            dur_ms = (time.perf_counter() - t0) * 1000.0
            return "local_architect", f"Architectural Trigger matched: '{matched}' -> Local Reasoning Model (RTX 5090)", 0.70, dur_ms

        local_match = self.local_regex.search(user_text)
        if local_match:
            matched = local_match.group(0)
            dur_ms = (time.perf_counter() - t0) * 1000.0
            return "local_builder", f"Builder Trigger matched: '{matched}' -> Local Synthesizer (RTX 5090)", 0.05, dur_ms

        dur_ms = (time.perf_counter() - t0) * 1000.0
        return "local_builder", "Ambiguous query defaulted to Local Builder (Maximize RTX 5090 VRAM)", 0.20, dur_ms


# Pre-compile singleton classifier instance at module load
CLASSIFIER = LexicalClassifier()


def route_request(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    requested_model: Optional[str] = None,
    hardware_info: Optional[Dict[str, Any]] = None,
    biasing_state: Optional[Dict[str, Any]] = None
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
    biasing_state = biasing_state or {}

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

    # Explicit Role & Lane Intent Dispatch (Phase 0)
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                user_text = c
            elif isinstance(c, list):
                user_text = " ".join([p.get("text", "") for p in c if isinstance(p, dict) and "text" in p])
            break

    user_lower = user_text.lower()
    has_tool_history = any(m.get("role") == "tool" or "tool_call_id" in m for m in messages)

    # Architect Lane intent (Section 4.1: @architect, /plan, [architect], milestone reviews, system setup)
    is_architect_intent = (
        "@architect" in user_lower or
        "[architect]" in user_lower or
        user_lower.startswith("/plan") or
        "milestone review" in user_lower or
        "milestone roadmap" in user_lower or
        "architectural standpoint" in user_lower or
        "architectural review" in user_lower
    )

    if is_architect_intent:
        return {
            "route": "cloud",
            "target_role": "architect",
            "phase": 0,
            "reason": "Intent trigger matched: Architect Lane (@architect / /plan / milestone review)",
            "complexity_score": 1.0,
            "latency_ms": round((time.perf_counter() - t_start) * 1000.0, 3)
        }

    # Builder Lane intent (subagent tasks, tool execution cycles, coder turns)
    is_builder_intent = (
        "@coder" in user_lower or
        "@builder" in user_lower or
        "@test_runner" in user_lower or
        "[builder]" in user_lower or
        "builder" in req_lower or
        has_tool_history
    )

    if is_builder_intent:
        return {
            "route": "local_builder",
            "target_role": "builder",
            "phase": 0,
            "reason": "Intent trigger matched: Builder Lane (Subagent / Tool Cycle / Code Loop)",
            "complexity_score": 0.1,
            "latency_ms": round((time.perf_counter() - t_start) * 1000.0, 3)
        }

    # Phase 1: Structural & Hardware Validation (<1ms)
    is_valid_for_local, escalation_reason = validate_structure(messages, tools, hardware_info)
    if not is_valid_for_local:
        return {
            "route": "cloud",
            "target_role": "cloud",
            "phase": 1,
            "reason": escalation_reason,
            "complexity_score": 1.0,
            "latency_ms": (time.perf_counter() - t_start) * 1000.0
        }

    # Phase 2: Ultra-Fast Lexical Scan (1-2ms)
    route, reason, complexity_score, scan_latency = CLASSIFIER.classify_with_latency(user_text)

    # Biasing Override Check
    biasing_mode = biasing_state.get("mode", "adaptive")
    if biasing_mode == "local_only":
        if route == "cloud":
            route = "local_builder"
        reason += " [Strict 100% Local Mode]"

    total_latency_ms = (time.perf_counter() - t_start) * 1000.0
    target_role = "architect" if "architect" in route else "cloud" if route == "cloud" else "builder"
    return {
        "route": route,
        "target_role": target_role,
        "phase": 2,
        "reason": reason,
        "complexity_score": complexity_score,
        "latency_ms": round(total_latency_ms, 3)
    }


def classify_intent_and_lane(messages: List[Dict[str, Any]]) -> Tuple[str, bool, str]:
    """
    Determines Architect vs Builder execution lane.
    Returns: (lane, should_use_cloud, reason)
    """
    res = route_request(messages)
    route = res.get("route", "local")
    is_cloud = (route == "cloud")
    lane = "architect" if (is_cloud or "architect" in route) else "builder"
    return lane, is_cloud, res.get("reason", "")


def decide_model(messages: List[Dict[str, Any]], dynamic_bias: float = 0.0) -> Tuple[str, bool, str]:
    """
    Resolves model and routing decision.
    Returns: (model_name, should_use_cloud, reason)
    """
    lane, should_cloud, reason = classify_intent_and_lane(messages)
    if should_cloud:
        return "gemini-2.5-pro", True, reason
    return "qwen2.5-coder:32b", False, reason

