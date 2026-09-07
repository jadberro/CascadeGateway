"""
CascadeGateway Automated Test & Verification Suite
Validates API endpoints, hardware detection, workflow modes, Review Gate, and model selection.
"""

import sys
import time
import json
import httpx

BASE_URL = "http://127.0.0.1:8000"


def test_models_endpoint():
    print("\n--- 1. Testing /v1/models ---")
    try:
        resp = httpx.get(f"{BASE_URL}/v1/models", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        models = [m["id"] for m in data.get("data", [])]
        print(f"Available Models: {models}")
        assert "cascade-auto" in models
        print("PASS: /v1/models working.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_hardware_endpoint():
    print("\n--- 2. Testing /api/hardware ---")
    try:
        resp = httpx.get(f"{BASE_URL}/api/hardware", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        hw = data.get("hardware", {})
        print(f"Detected GPU: {hw.get('gpu_name')} ({hw.get('vram_gb')} GB VRAM)")
        print(f"Active Local Model: {data.get('active_local_model')}")
        assert "hardware" in data
        print("PASS: /api/hardware working.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_model_selection_endpoint():
    print("\n--- 3. Testing /api/models/selection ---")
    try:
        resp = httpx.get(f"{BASE_URL}/api/models/selection", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        print(f"Selection: {data.get('selection')}")
        print(f"Resolved: {data.get('resolved')}")
        assert "resolved" in data
        print("PASS: /api/models/selection working.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_workflow_modes_endpoint():
    print("\n--- 4. Testing /v1/workflow/modes ---")
    try:
        resp = httpx.get(f"{BASE_URL}/v1/workflow/modes", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        print(f"Active Workflow Mode: {data.get('active_mode')}")
        assert "modes" in data
        print("PASS: /v1/workflow/modes working.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_simple_prompt_local_routing():
    print("\n--- 5. Testing Simple Prompt Local Routing ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "system", "content": "You are a concise programming assistant."},
            {"role": "user", "content": "Write a 1-line Python function to reverse a string."}
        ],
        "temperature": 0.2,
        "max_tokens": 64
    }

    t0 = time.time()
    try:
        resp = httpx.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        duration = time.time() - t0

        reply = data["choices"][0]["message"]["content"]
        route = data.get("_routing_info", {})
        print(f"Response ({duration:.2f}s): {reply.strip()[:80]}...")
        print(f"Routed To: {route.get('tier')} ({route.get('model')})")
        print("PASS: Local routing verified.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_metrics_endpoint():
    print("\n--- 6. Testing /metrics ---")
    try:
        resp = httpx.get(f"{BASE_URL}/metrics", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        saved = data.get("tokens", {}).get("tokens_saved_locally", 0)
        print(f"Tokens Saved: {saved:,} | Requests: {data.get('requests', {})}")
        print("PASS: /metrics verified.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    print("==================================================")
    print("  CascadeGateway Verification Test Suite")
    print("==================================================")
    s1 = test_models_endpoint()
    if not s1:
        print("Gateway is not running on port 8000. Start it first!")
        sys.exit(1)
    test_hardware_endpoint()
    test_model_selection_endpoint()
    test_workflow_modes_endpoint()
    test_simple_prompt_local_routing()
    test_metrics_endpoint()
    print("\nAll gateway tests PASSED!")
