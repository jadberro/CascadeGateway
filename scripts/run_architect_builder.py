#!/usr/bin/env python3
"""
scripts/run_architect_builder.py
Hybrid Architect-Builder Execution Harness for Antigravity & CascadeGateway.

- Architect: Cloud Pro Model (Milestone planning, AST decomposition, review).
- Builder: Local RTX 5090 (Qwen2.5-Coder-32B via CascadeGateway / vLLM / Ollama).
Supports both native Antigravity SDK and standalone OpenAI-compatible HTTP fallback.
"""

import os
import sys
import time
import json
import argparse
import asyncio
from typing import Dict, Any, Optional

import httpx

# Try importing Google Antigravity SDK
try:
    from google.antigravity import Agent, AgentConfig, LocalOpenAIAgentConfig
    from google.antigravity.config import CompactionConfig
    ANTIGRAVITY_SDK_AVAILABLE = True
except ImportError:
    ANTIGRAVITY_SDK_AVAILABLE = False


DEFAULT_BASE_URL = os.getenv("CASCADE_GATEWAY_URL", "http://127.0.0.1:8000/v1")
DEFAULT_API_KEY = os.getenv("CASCADE_GATEWAY_KEY", "local-builder-token")


def parse_args():
    parser = argparse.ArgumentParser(description="Hybrid Architect-Builder Execution Harness")
    parser.add_argument(
        "--task",
        type=str,
        default="Draft a minimal 3-step file implementation plan for our target module. Output file paths and function signatures only. Do not write implementation code.",
        help="Architect planning prompt."
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Force standalone OpenAI-compatible HTTP client mode."
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help="CascadeGateway base URL (default: http://127.0.0.1:8000/v1)"
    )
    return parser.parse_args()


async def run_with_antigravity_sdk(task: str, base_url: str):
    """Execute using native Google Antigravity SDK bindings."""
    print("\n[Mode] Running via Native Google Antigravity SDK...")

    builder_config = LocalOpenAIAgentConfig(
        model="Qwen/Qwen2.5-Coder-32B-Instruct-AWQ",
        base_url=base_url,
        api_key=DEFAULT_API_KEY,
        compaction=CompactionConfig(token_threshold=30000),
    )

    architect_config = AgentConfig(
        model="gemini-2.5-pro",
        compaction=CompactionConfig(token_threshold=15000),
    )

    async with Agent(architect_config) as architect, Agent(builder_config) as builder:
        print("\n[Phase 1: Cloud Architect] Generating implementation milestone roadmap...")
        t0 = time.perf_counter()
        plan = await architect.chat(task)
        plan_text = await plan.text() if hasattr(plan, "text") else str(plan)
        t_arch = time.perf_counter() - t0
        print(f"[Architect Plan ({t_arch:.2f}s)]:\n{plan_text}\n")

        print("[Phase 2: Local Builder (RTX 5090)] Executing implementation with zero cloud token cost...")
        t1 = time.perf_counter()
        build_result = await builder.chat(
            f"Implement the following plan in the local workspace. Run tests and verify syntax:\n{plan_text}"
        )
        build_text = await build_result.text() if hasattr(build_result, "text") else str(build_result)
        t_build = time.perf_counter() - t1
        print(f"[Builder Output ({t_build:.2f}s)]:\n{build_text}\n")


async def run_standalone_http(task: str, base_url: str):
    """Execute using CascadeGateway HTTP REST API."""
    print(f"\n[Mode] Running via CascadeGateway OpenAI-Compatible API ({base_url})...")
    chat_url = f"{base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=180.0) as client:
        # Step 1: Cloud Architect
        print("\n[Phase 1: Cloud Architect] Generating milestone specification...")
        t0 = time.perf_counter()
        arch_payload = {
            "model": "gemini-2.5-pro",
            "messages": [
                {
                    "role": "system",
                    "content": "You are the Cloud Architect. High-level system decomposition and milestone planning only. Output concise file paths and signatures."
                },
                {
                    "role": "user",
                    "content": f"@architect {task}"
                }
            ],
            "temperature": 0.2
        }

        try:
            arch_resp = await client.post(
                chat_url,
                json=arch_payload,
                headers={"Authorization": f"Bearer {DEFAULT_API_KEY}"}
            )
            arch_resp.raise_for_status()
            arch_data = arch_resp.json()
            arch_text = arch_data["choices"][0]["message"]["content"]
            arch_tokens = arch_data.get("usage", {}).get("total_tokens", 0)
        except Exception as e:
            print(f"[Architect Error] {e}")
            arch_text = f"Fallback Plan:\n1. Create src/module.py with interface definition.\n2. Add test suite in tests/test_module.py."
            arch_tokens = 0

        t_arch = time.perf_counter() - t0
        print(f"Architect Response ({t_arch:.2f}s, ~{arch_tokens} tokens):\n{arch_text}\n")

        # Step 2: Local Builder on RTX 5090
        print("[Phase 2: Local Builder (RTX 5090)] Executing code generation locally ($0 cloud tokens)...")
        t1 = time.perf_counter()
        build_payload = {
            "model": "qwen2.5-coder:32b",
            "messages": [
                {
                    "role": "system",
                    "content": "You are the Local Builder. Write complete, robust code implementing the specified plan."
                },
                {
                    "role": "user",
                    "content": f"@builder Implement the following architectural plan:\n\n{arch_text}"
                }
            ],
            "temperature": 0.1
        }

        try:
            build_resp = await client.post(
                chat_url,
                json=build_payload,
                headers={"Authorization": f"Bearer {DEFAULT_API_KEY}"}
            )
            build_resp.raise_for_status()
            build_data = build_resp.json()
            build_text = build_data["choices"][0]["message"]["content"]
            build_tokens = build_data.get("usage", {}).get("total_tokens", 0)
        except Exception as e:
            print(f"[Builder Error] {e}")
            build_text = "Builder encountered error: " + str(e)
            build_tokens = 0

        t_build = time.perf_counter() - t1
        print(f"Builder Response ({t_build:.2f}s, ~{build_tokens} tokens locally):\n{build_text}\n")

        # Telemetry & Cost Breakdown
        print("====================================================================")
        print(" HYBRID ARCHITECT-BUILDER SESSION SUMMARY")
        print("====================================================================")
        print(f" Cloud Architect:  {arch_tokens} tokens  | {t_arch:.2f}s | Cloud Quota Used")
        print(f" Local Builder:    {build_tokens} tokens  | {t_build:.2f}s | $0.00 Cloud Cost (RTX 5090)")
        print(f" Token Efficiency: ~{build_tokens} heavy implementation tokens diverted from Cloud")
        print("====================================================================")


def main():
    args = parse_args()
    if ANTIGRAVITY_SDK_AVAILABLE and not args.standalone:
        asyncio.run(run_with_antigravity_sdk(args.task, args.base_url))
    else:
        asyncio.run(run_standalone_http(args.task, args.base_url))


if __name__ == "__main__":
    main()
