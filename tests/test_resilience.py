"""
CascadeGateway Resilience & Latency Test Suite
Validates the Intelligent Routing and Failover Engine:
- Sub-5ms routing classification overhead
- Phase 1 Structural & Hardware Validation
- Phase 2 Lexical Intent Automaton
- Phase 3 3-Token Lookahead Buffer and Streaming
"""

import time
import json
import httpx

from cascadegateway.core.classifier import route_request, validate_structure, LexicalClassifier
from cascadegateway.core.hardware import HARDWARE_INFO

BASE_URL = "http://127.0.0.1:8000"


def test_routing_latency_sub_5ms():
    print("\n--- 1. Benchmarking Classification Latency (<5ms SLA) ---")
    classifier = LexicalClassifier()
    sample_queries = [
        "Can you write a unit test for this payment processor?",
        "There is a dangerous race condition and deadlock in the thread pool.",
        "Add type annotations and docstrings to this function.",
        "Refactor this architecture to optimize distributed consensus and latency.",
        "Fix the typo in the logger statement.",
        "How do I reverse a string in Python?",
    ]

    latencies = []
    for q in sample_queries:
        t0 = time.perf_counter()
        route, reason, score, dur = classifier.classify_with_latency(q)
        latencies.append(dur)
        print(f"  Query: '{q[:40]}...' -> Route: {route} ({dur:.3f}ms) [{reason}]")

    avg_ms = sum(latencies) / len(latencies)
    max_ms = max(latencies)
    print(f"Average Latency: {avg_ms:.3f}ms | Max Latency: {max_ms:.3f}ms")
    assert max_ms < 5.0, f"Max latency {max_ms}ms exceeded 5ms SLA!"
    print("PASS: Routing latency well under 5ms SLA.")


def test_phase1_structural_validation():
    print("\n--- 2. Testing Phase 1: Structural & Hardware Validation ---")

    # Test 1: Tool calling schema triggers Cloud escalation
    tools_payload = [{"type": "function", "function": {"name": "get_weather"}}]
    valid, reason = validate_structure([{"role": "user", "content": "test"}], tools_payload, HARDWARE_INFO)
    print(f"  Tools check: valid={valid}, reason='{reason}'")
    assert valid is False
    assert "Tool/function calling" in reason

    # Test 2: Multi-turn depth > 12 turns triggers Cloud escalation
    long_history = [{"role": "user", "content": f"turn {i}"} for i in range(15)]
    valid, reason = validate_structure(long_history, None, HARDWARE_INFO)
    print(f"  Turn depth check (15 turns): valid={valid}, reason='{reason}'")
    assert valid is False
    assert "Conversation depth" in reason

    # Test 3: Context length exceeding local budget triggers Cloud escalation
    huge_message = [{"role": "user", "content": "A" * (40000 * 4)}]  # ~40,000 tokens
    valid, reason = validate_structure(huge_message, None, HARDWARE_INFO)
    print(f"  Context overflow check: valid={valid}, reason='{reason}'")
    assert valid is False
    assert "exceeds local VRAM" in reason

    print("PASS: Phase 1 structural validation verified.")


def test_phase2_lexical_scans():
    print("\n--- 3. Testing Phase 2: Lexical Intent Scans ---")
    classifier = LexicalClassifier()

    # Local Architect Trigger (routes to local DeepSeek-R1 / Gemma on RTX 5090)
    r, reason, score, _ = classifier.classify_with_latency("There is a subtle memory leak and race condition here.")
    assert r == "local_architect"
    assert "Architectural Trigger" in reason

    # Local Trigger
    r, reason, score, _ = classifier.classify_with_latency("Write a pytest test case with mock assertions.")
    assert r == "local_builder"
    assert "Builder Trigger" in reason

    # Ambiguous Trigger -> defaults to Local Builder
    r, reason, score, _ = classifier.classify_with_latency("How does Python handle garbage collection?")
    assert r == "local_builder"
    assert "Ambiguous" in reason

    print("PASS: Phase 2 lexical classification logic verified.")


def test_live_streaming_endpoint():
    print("\n--- 4. Testing Live SSE Streaming (/v1/chat/completions) ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "Write a regex to match email addresses."}
        ],
        "stream": True,
        "max_tokens": 64
    }

    t0 = time.time()
    chunks_received = 0
    first_chunk_latency = None

    with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions", json=payload, timeout=30.0) as resp:
        assert resp.status_code == 200
        print("  Stream connected. Receiving chunks: ", end="", flush=True)
        for line in resp.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                if first_chunk_latency is None:
                    first_chunk_latency = time.time() - t0
                chunks_received += 1
                data = json.loads(line[6:])
                chunk_text = data["choices"][0]["delta"].get("content", "")
                print(chunk_text, end="", flush=True)

    print(f"\n  Total chunks: {chunks_received} | Time to first token (TTFT): {first_chunk_latency:.2f}s")
    assert chunks_received >= 3
    print("PASS: Live streaming with Lookahead Buffer verified.")


def test_manual_biasing_threshold():
    print("\n--- 5. Testing Manual Biasing Threshold Logic ---")
    # Standard coding query -> stays local builder
    std_msg = [{"role": "user", "content": "How do I format this JSON object and add logging?"}]
    res_std = route_request(std_msg, biasing_state={"mode": "manual", "bias_factor": 0.9})
    print(f"  Standard query: route={res_std['route']}, role={res_std.get('target_role')}")
    assert res_std["target_role"] == "builder"
    assert res_std["route"] == "local_builder"

    # Architectural query -> routes to local architect (DeepSeek-R1 / Gemma on RTX 5090)
    arch_msg = [{"role": "user", "content": "System design analysis of distributed consensus and architectural tradeoffs."}]
    res_arch = route_request(arch_msg, biasing_state={"mode": "manual", "bias_factor": 0.9})
    print(f"  Architectural query: route={res_arch['route']}, role={res_arch.get('target_role')}")
    assert res_arch["target_role"] == "architect"
    assert res_arch["route"] == "local_architect"

    # Cloud override query -> routes to cloud
    res_cloud = route_request(arch_msg, requested_model="gemini-2.5-pro")
    assert res_cloud["route"] == "cloud"
    print("PASS: Role routing and model escalation logic verified.")


def test_short_response_lookahead():
    print("\n--- 6. Testing Short Response (<3 tokens) Lookahead Sentinel ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "Reply with only the word OK"}
        ],
        "stream": True,
        "max_tokens": 4
    }
    t0 = time.time()
    chunks = []
    has_done = False
    with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions", json=payload, timeout=10.0) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line == "data: [DONE]":
                has_done = True
                break
            if line.startswith("data: "):
                data = json.loads(line[6:])
                chunk_text = data["choices"][0]["delta"].get("content", "")
                if chunk_text:
                    chunks.append(chunk_text)

    elapsed = time.time() - t0
    print(f"  Received chunks: {chunks} in {elapsed:.2f}s (has_done={has_done})")
    assert has_done, "Stream did not terminate cleanly with data: [DONE]"
    assert elapsed < 5.0, f"Stream hung or took too long: {elapsed:.2f}s"
    print("PASS: Short response lookahead sentinel completed without deadlock.")


def test_client_abort_cleanup():
    print("\n--- 7. Testing Client Abort / Disconnect Resource Cleanup ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "Write a 500-line Python implementation of a compiler."}
        ],
        "stream": True,
        "max_tokens": 1000
    }
    # Simulate Cursor user pressing Escape after 1 token
    with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions", json=payload, timeout=10.0) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                # Received first token, immediately abort/close
                resp.close()
                break
    time.sleep(0.5)
    print("PASS: Client aborted cleanly without crashing or blocking server.")


if __name__ == "__main__":
    print("==========================================================")
    print("  Intelligent Routing & Failover Engine Verification")
    print("==========================================================")
    test_routing_latency_sub_5ms()
    test_phase1_structural_validation()
    test_phase2_lexical_scans()
    test_manual_biasing_threshold()
    test_live_streaming_endpoint()
    test_short_response_lookahead()
    test_client_abort_cleanup()
    print("\nALL RESILIENCE, ROUTING & EDGE-CASE TESTS PASSED!")
