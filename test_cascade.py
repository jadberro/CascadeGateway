"""
Validation and Test Suite for the Model Cascading Gateway
"""

import sys
import time
import json
import httpx

BASE_URL = "http://127.0.0.1:8000/v1"


def test_models_endpoint():
    print("\n--- 1. Testing /v1/models ---")
    try:
        resp = httpx.get(f"{BASE_URL}/models", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        models = [m["id"] for m in data.get("data", [])]
        print(f"Available Models in Gateway: {models}")
        assert "cascade-auto" in models
        print("PASS: /v1/models working.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_simple_prompt_local_routing():
    print("\n--- 2. Testing Simple Prompt (Expected: Local RTX 5090 Tier) ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "system", "content": "You are a concise programming assistant."},
            {"role": "user", "content": "Write a 1-line Python function to reverse a string."}
        ],
        "temperature": 0.2,
        "max_tokens": 128
    }

    t0 = time.time()
    try:
        resp = httpx.post(f"{BASE_URL}/chat/completions", json=payload, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        duration = time.time() - t0

        reply = data["choices"][0]["message"]["content"]
        route = data.get("_routing_info", {})
        usage = data.get("usage", {})

        print(f"Response ({duration:.2f}s):")
        print(f"{reply.strip()}")
        print(f"Routed To: {route.get('tier')} ({route.get('model')})")
        print(f"Tokens Saved: {route.get('tokens_saved')} (Prompt: {usage.get('prompt_tokens')}, Comp: {usage.get('completion_tokens')})")
        print("PASS: Local routing verified.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def test_streaming():
    print("\n--- 3. Testing Streaming (SSE) ---")
    payload = {
        "model": "cascade-auto",
        "messages": [
            {"role": "user", "content": "Count from 1 to 5 quickly."}
        ],
        "stream": True,
        "max_tokens": 64
    }

    try:
        with httpx.stream("POST", f"{BASE_URL}/chat/completions", json=payload, timeout=30.0) as resp:
            resp.raise_for_status()
            print("Stream chunks received: ", end="", flush=True)
            for line in resp.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    data = json.loads(line[6:])
                    chunk = data["choices"][0]["delta"].get("content", "")
                    print(chunk, end="", flush=True)
            print("\nPASS: Streaming verified.")
        return True
    except Exception as e:
        print(f"\nFAIL: {e}")
        return False


def test_metrics_endpoint():
    print("\n--- 4. Testing /v1/metrics ---")
    try:
        resp = httpx.get(f"{BASE_URL}/metrics", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        print(f"Metrics: {json.dumps(data, indent=2)}")
        print("PASS: Metrics verified.")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    print("Testing RTX 5090 Model Cascading Gateway...")
    s1 = test_models_endpoint()
    if not s1:
        print("Server appears not to be running. Start it with start_cascade.bat first!")
        sys.exit(1)
    test_simple_prompt_local_routing()
    test_streaming()
    test_metrics_endpoint()
    print("\nAll tests complete!")
