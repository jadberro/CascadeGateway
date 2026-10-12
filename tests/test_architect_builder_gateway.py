"""
tests/test_architect_builder_gateway.py
Comprehensive test suite for Hybrid Architect-Builder Execution:
- Context Pruning & Deduplication (pruner.py)
- Intent & Lane Classification (classifier.py)
- Universal Local Backend Detection (router.py)
- Telemetry Logging & Audit Reporting (audit.py)
"""

import os
import json
from pathlib import Path
import pytest

from cascadegateway.core.pruner import (
    clamp_terminal_output,
    deduplicate_file_buffers,
    prune_messages,
    estimate_tokens,
)
from cascadegateway.core.classifier import classify_intent_and_lane, decide_model
from cascadegateway.core.router import probe_and_resolve_local_backend
from cascadegateway.audit import log_telemetry_event, compare_sessions, generate_sample_session_logs


# =====================================================================
# 1. Context Pruning Tests
# =====================================================================
def test_clamp_terminal_output_under_threshold():
    short_log = "Build started...\nCompiling src/main.rs\nBuild finished in 0.12s\n"
    clamped, omitted = clamp_terminal_output(short_log, max_lines=60)
    assert clamped == short_log
    assert omitted == 0


def test_clamp_terminal_output_over_threshold():
    lines = [f"LOG line {i}: compiling item {i}" for i in range(100)]
    long_log = "\n".join(lines)
    clamped, omitted = clamp_terminal_output(long_log, max_lines=60, head_lines=15, tail_lines=30)
    
    assert omitted == 55
    assert "clipped by CascadeGateway: 55 lines omitted" in clamped
    assert "LOG line 0:" in clamped
    assert "LOG line 14:" in clamped
    assert "LOG line 99:" in clamped
    assert "LOG line 50:" not in clamped


def test_deduplicate_file_buffers_replaces_identical_turns():
    code_block = "```python\n" + "\n".join([f"def func_{i}(): pass" for i in range(15)]) + "\n```"
    
    messages = [
        {"role": "user", "content": f"Here is the file:\n{code_block}"},
        {"role": "assistant", "content": "I see the code."},
        {"role": "user", "content": f"Update func_2 in this file:\n{code_block}"},
    ]
    
    deduped, count = deduplicate_file_buffers(messages)
    assert count == 1
    assert "File content identical to turn 0; omitted for token efficiency" in deduped[2]["content"]
    assert code_block in deduped[0]["content"]


def test_prune_messages_end_to_end_calculates_savings():
    code_block = "```python\n" + "\n".join([f"x_{i} = {i} * 2" for i in range(20)]) + "\n```"
    long_terminal = "\n".join([f"step {i}: status ok" for i in range(80)])
    
    messages = [
        {"role": "user", "content": f"Run this:\n{code_block}\nTerminal output:\n{long_terminal}"},
        {"role": "assistant", "content": "Done."},
        {"role": "user", "content": f"Same file again:\n{code_block}"},
    ]
    
    stats = prune_messages(messages)
    pruned_msgs = stats["pruned_messages"]
    assert stats["clamped_lines"] >= 1
    assert stats["dedup_files"] >= 1
    assert stats["tokens_saved"] > 0
    assert stats["pruned_tokens"] < stats["raw_tokens"]
    assert len(pruned_msgs) == 3


# =====================================================================
# 2. Classifier Lane Routing Tests
# =====================================================================
def test_architect_lane_routing():
    prompts = [
        [{"role": "user", "content": "@architect Draft a minimal 3-step file implementation plan"}],
        [{"role": "user", "content": "/plan Architectural roadmap for auth module"}],
        [{"role": "user", "content": "[architect] High-level system decomposition"}],
        [{"role": "user", "content": "Please review this milestone roadmap from an architectural standpoint"}]
    ]
    
    for msgs in prompts:
        lane, should_cloud, reason = classify_intent_and_lane(msgs)
        assert lane == "architect", f"Failed for {msgs[0]['content']}: lane={lane}, reason={reason}"
        assert should_cloud is True
        
        # Test full decide_model
        model, used_cloud, _ = decide_model(msgs)
        assert used_cloud is True
        assert "gemini" in model.lower()


def test_builder_lane_routing():
    prompts = [
        [{"role": "user", "content": "@builder Implement the plan and fix syntax"}],
        [{"role": "user", "content": "@coder Write the unit tests for auth.py"}],
        [{"role": "user", "content": "@test_runner Run pytest on tests/test_auth.py"}],
        [{"role": "user", "content": "[builder] Apply the file diffs"}],
        [
            {"role": "user", "content": "Run tests"},
            {"role": "assistant", "content": "Running...", "tool_calls": [{"id": "call_1", "function": {"name": "run_test"}}]},
            {"role": "tool", "content": "FAILED: assert 1 == 2", "tool_call_id": "call_1"}
        ]
    ]
    
    for msgs in prompts:
        lane, should_cloud, reason = classify_intent_and_lane(msgs)
        assert lane == "builder", f"Failed for {msgs[-1]}"
        assert should_cloud is False
        
        model, used_cloud, _ = decide_model(msgs)
        assert used_cloud is False
        assert "qwen" in model.lower() or "local" in model.lower() or "5090" in model.lower()


# =====================================================================
# 3. Universal Local Backend Detection Tests
# =====================================================================
def test_probe_and_resolve_local_backend():
    import asyncio
    backend = asyncio.run(probe_and_resolve_local_backend())
    assert isinstance(backend, dict)
    assert "type" in backend
    assert "base_url" in backend
    assert backend["type"] in ("ollama", "vllm", "lmstudio")


# =====================================================================
# 4. Telemetry and Audit Tests
# =====================================================================
def test_log_telemetry_event(tmp_path):
    log_file = tmp_path / "telemetry_test.jsonl"
    event = {
        "event": "architect_turn",
        "lane": "architect",
        "model": "gemini-2.5-pro",
        "raw_tokens": 1200,
        "pruned_tokens": 800,
        "tokens_saved": 400
    }
    
    log_telemetry_event(event, file_path=log_file)
    assert log_file.exists()
    
    with open(log_file, "r", encoding="utf-8") as f:
        line = f.readline()
        data = json.loads(line)
        assert data["event"] == "architect_turn"
        assert data["tokens_saved"] == 400


def test_compare_sessions(tmp_path):
    base_file = tmp_path / "baseline.jsonl"
    opt_file = tmp_path / "optimized.jsonl"
    generate_sample_session_logs(base_file, opt_file)
    
    report = compare_sessions(base_file, opt_file)
    assert "HYBRID ARCHITECT-BUILDER TELEMETRY AUDIT REPORT" in report
    assert "Cloud Token Reduction" in report
    assert "Local RTX 5090 Execution Ratio" in report
    assert "%" in report
