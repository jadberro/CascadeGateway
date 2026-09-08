"""
CascadeGateway Engine Hardening, Observability & Verification Test Suite
Tests:
1. Sliding-window loop detection ring buffer
2. Diagnostic routing headers (X-Cascade-*)
3. IDE Auto-configuration endpoint (/api/ide/auto-config)
4. Exact token metrics and dollar savings calculation (/metrics)
5. Asymmetric Verification pipeline (/verify)
"""

import time
import httpx

from cascadegateway.core.streaming import detect_repetition_loop

BASE_URL = "http://127.0.0.1:8000"


def test_sliding_window_loop_detection():
    print("\n--- 1. Testing Sliding-Window Repetition Loop Breaker ---")

    # Positive test: pattern len 2 repeated 4x
    assert detect_repetition_loop(["{", "}"] * 4) is True, "Failed to detect len 2 pattern 4x"
    # Positive test: pattern len 3 repeated 4x
    assert detect_repetition_loop(["def", " ", "foo"] * 4) is True, "Failed to detect len 3 pattern 4x"
    # Positive test: pattern len 4 repeated 4x
    assert detect_repetition_loop(["a", "b", "c", "d"] * 4) is True, "Failed to detect len 4 pattern 4x"

    # Negative test: pattern len 2 repeated only 3x
    assert detect_repetition_loop(["{", "}"] * 3) is False, "False positive on 3x repetition"
    # Negative test: normal non-repeating code tokens
    normal_tokens = ["def", " ", "add", "(", "a", ",", " ", "b", ")", ":", "\n", "    return", " ", "a", " ", "+"]
    assert detect_repetition_loop(normal_tokens) is False, "False positive on normal code"

    print("PASS: Sliding-window loop detector verified (lens 2, 3, 4 with 4x threshold).")


def test_diagnostic_routing_headers():
    print("\n--- 2. Testing Diagnostic Routing Headers (X-Cascade-*) ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "Write a 1-line Python lambda to square a number."}
        ],
        "stream": True,
        "max_tokens": 32
    }

    with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions", json=payload, timeout=15.0) as resp:
        assert resp.status_code == 200
        headers = resp.headers
        print(f"  X-Cascade-Route: {headers.get('x-cascade-route')}")
        print(f"  X-Cascade-Model: {headers.get('x-cascade-model')}")
        print(f"  X-Cascade-Decision-MS: {headers.get('x-cascade-decision-ms')}ms")
        print(f"  X-Cascade-Reason: {headers.get('x-cascade-reason')}")

        assert "x-cascade-route" in headers, "Missing X-Cascade-Route header"
        assert "x-cascade-model" in headers, "Missing X-Cascade-Model header"
        assert "x-cascade-decision-ms" in headers, "Missing X-Cascade-Decision-MS header"
        assert "x-cascade-reason" in headers, "Missing X-Cascade-Reason header"

    print("PASS: Diagnostic routing headers verified on streaming completions.")


def test_ide_auto_config_endpoint():
    print("\n--- 3. Testing 1-Click IDE Auto-Config Endpoint ---")
    resp = httpx.post(f"{BASE_URL}/api/ide/auto-config", timeout=5.0)
    assert resp.status_code == 200
    data = resp.json()
    print(f"  Response: {data}")
    assert data.get("success") is True
    assert "continue_configured" in data
    assert "details" in data
    assert data.get("endpoint") == "http://127.0.0.1:8000/v1"
    print("PASS: /api/ide/auto-config endpoint functional.")


def test_dollar_savings_metrics():
    print("\n--- 4. Testing Exact Hardware Token Metrics & Dollar Savings ---")
    resp = httpx.get(f"{BASE_URL}/metrics", timeout=5.0)
    assert resp.status_code == 200
    data = resp.json()
    savings = data.get("savings", {})
    print(f"  Savings Telemetry: {savings}")
    assert "estimated_dollars_saved" in savings
    assert "cost_basis" in savings
    assert savings["estimated_dollars_saved"] >= 0.0
    print("PASS: Exact hardware metrics & dollar savings verified.")


def test_asymmetric_verification_routing():
    print("\n--- 5. Testing Asymmetric Verification Routing (/verify) ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "/verify Write a thread-safe singleton in Python."}
        ],
        "stream": True,
        "max_tokens": 128
    }

    with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions", json=payload, timeout=30.0) as resp:
        assert resp.status_code == 200
        headers = resp.headers
        print(f"  Pipeline Header: {headers.get('x-cascade-pipeline')}")
        print(f"  Route Header: {headers.get('x-cascade-route')}")
        print(f"  Model Header: {headers.get('x-cascade-model')}")

        assert headers.get("x-cascade-pipeline") == "asymmetric-verification"
        assert headers.get("x-cascade-route") == "asymmetric-verification"

    print("PASS: Asymmetric Verification (/verify) pipeline verified.")


if __name__ == "__main__":
    print("==========================================================")
    print("  CascadeGateway New Features & Hardening Verification")
    print("==========================================================")
    test_sliding_window_loop_detection()
    test_ide_auto_config_endpoint()
    test_dollar_savings_metrics()
    test_diagnostic_routing_headers()
    test_asymmetric_verification_routing()
    print("\nALL NEW FEATURES & HARDENING TESTS PASSED!")
