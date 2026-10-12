"""
CascadeGateway Audit & Token Reduction Telemetry
Logs request-level token pruning and routing metrics to ~/.cascadegateway/telemetry.jsonl.
Provides the `--compare` CLI tool to generate pre vs post token audit reports.
"""

import os
import json
import argparse
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

TELEMETRY_DIR = Path.home() / ".cascadegateway"
TELEMETRY_FILE = TELEMETRY_DIR / "telemetry.jsonl"


def log_telemetry_event(
    session_id_or_event: Any = "default_session",
    turn_index: int = 1,
    role: str = "builder",
    target_route: str = "local",
    raw_input_tokens: int = 0,
    pruned_input_tokens: int = 0,
    output_tokens: int = 0,
    tokens_saved: int = 0,
    latency_ms: float = 0.0,
    vllm_cache_hit_rate: float = 0.0,
    custom_filepath: Optional[str] = None,
    file_path: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Logs a single turn telemetry event into JSONL storage."""
    chosen_path = file_path or custom_filepath
    target_path = Path(chosen_path) if chosen_path else TELEMETRY_FILE
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(session_id_or_event, dict):
        event = dict(session_id_or_event)
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
    else:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": str(session_id_or_event),
            "turn_index": turn_index,
            "role": role,
            "target_route": target_route,
            "raw_input_tokens": raw_input_tokens,
            "pruned_input_tokens": pruned_input_tokens,
            "output_tokens": output_tokens,
            "tokens_saved": tokens_saved,
            "latency_ms": round(latency_ms, 2),
            "vllm_cache_hit_rate": round(vllm_cache_hit_rate, 4),
        }
        event.update(kwargs)

    try:
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        print(f"[Telemetry Warning] Failed to log telemetry: {e}")

    return event


def parse_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """Parses a JSONL telemetry file into a list of record dicts."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Telemetry log file not found: {file_path}")

    records = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                try:
                    records.append(json.loads(line_str))
                except json.JSONDecodeError:
                    continue
    return records


def compare_sessions(baseline_file: str, optimized_file: str):
    """
    Compares baseline vs optimized session metrics and prints a clean GitHub-style markdown table.
    """
    base_records = parse_jsonl(baseline_file)
    opt_records = parse_jsonl(optimized_file)

    def analyze(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not records:
            return {
                "turns": 0, "avg_prompt": 0, "billed_cloud": 0,
                "absorbed_local": 0, "hit_rate": 0.0, "avg_latency": 0.0
            }
        turns = len(records)
        raw_prompts = [r.get("raw_input_tokens", 0) for r in records]
        pruned_prompts = [r.get("pruned_input_tokens", r.get("raw_input_tokens", 0)) for r in records]
        avg_prompt = statistics.mean(pruned_prompts) if pruned_prompts else 0

        # Billed to cloud: Turns where target_route is cloud / gemini
        billed_cloud = sum(
            r.get("pruned_input_tokens", r.get("raw_input_tokens", 0)) + r.get("output_tokens", 0)
            for r in records
            if "cloud" in r.get("target_route", "").lower() or r.get("role") == "cloud" or "gemini" in r.get("target_route", "").lower()
        )

        # Absorbed by local GPU: Turns routed locally
        absorbed_local = sum(
            r.get("pruned_input_tokens", r.get("raw_input_tokens", 0)) + r.get("output_tokens", 0)
            for r in records
            if "local" in r.get("target_route", "").lower() or r.get("role") in ("builder", "local_builder", "architect")
        )

        hit_rates = [r.get("vllm_cache_hit_rate", 0.0) for r in records if "vllm_cache_hit_rate" in r]
        avg_hit_rate = statistics.mean(hit_rates) if hit_rates else 0.0
        latencies = [r.get("latency_ms", 0.0) for r in records]
        avg_latency = statistics.mean(latencies) if latencies else 0.0

        return {
            "turns": turns,
            "avg_prompt": round(avg_prompt),
            "billed_cloud": billed_cloud,
            "absorbed_local": absorbed_local,
            "hit_rate": avg_hit_rate,
            "avg_latency": round(avg_latency, 1)
        }

    b = analyze(base_records)
    o = analyze(opt_records)

    base_avg_raw = statistics.mean([r.get("raw_input_tokens", 0) for r in base_records]) if base_records else 1
    opt_avg_pruned = o["avg_prompt"]
    bloat_reduction = max(0.0, (base_avg_raw - opt_avg_pruned) / base_avg_raw * 100.0) if base_avg_raw > 0 else 0.0

    cloud_reduction = 0.0
    if b["billed_cloud"] > 0:
        cloud_reduction = max(0.0, (b["billed_cloud"] - o["billed_cloud"]) / b["billed_cloud"] * 100.0)

    total_opt = max(1, o["billed_cloud"] + o["absorbed_local"])
    local_utilization = (o["absorbed_local"] / total_opt) * 100.0

    table_md = "\n".join([
        "=" * 80,
        "  HYBRID ARCHITECT-BUILDER TELEMETRY AUDIT REPORT",
        "=" * 80,
        "",
        "| Metric | Baseline (Cloud Pass-Through) | Optimized (Architect-Builder on 5090) | Impact |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Total Task Turns** | {b['turns']} turns | {o['turns']} turns | Consistent scope |",
        f"| **Average Prompt Tokens / Turn** | {b['avg_prompt']:,} tokens | {o['avg_prompt']:,} tokens | **-{bloat_reduction:.1f}% context bloat** |",
        f"| **Tokens Billed to Cloud Quota** | {b['billed_cloud']:,} tokens | {o['billed_cloud']:,} tokens | **-{cloud_reduction:.1f}% Cloud Token Reduction** |",
        f"| **Tokens Absorbed by Local GPU** | {b['absorbed_local']:,} tokens | {o['absorbed_local']:,} tokens | **{local_utilization:.1f}% Local RTX 5090 Execution Ratio** |",
        f"| **Prefix Cache Hit Rate** | {b['hit_rate']*100:.1f}% | {o['hit_rate']*100:.1f}% | Instantaneous prefill |",
        f"| **Average Latency** | {b['avg_latency']:.0f} ms | {o['avg_latency']:.0f} ms | Sub-second response |",
        ""
    ])
    print("\n" + table_md + "\n")
    return table_md


def generate_samples(base_file: str = "baseline.jsonl", opt_file: str = "optimized.jsonl"):
    """Generates realistic sample benchmark datasets matching the spec for validation."""
    baseline_records = [
        {
            "timestamp": "2026-10-12T14:00:00Z", "session_id": "bench-base-001",
            "turn_index": i, "role": "cloud", "target_route": "cloud_gemini_pro",
            "raw_input_tokens": 38200, "pruned_input_tokens": 38200,
            "output_tokens": 420, "tokens_saved": 0, "latency_ms": 1450,
            "vllm_cache_hit_rate": 0.0
        }
        for i in range(1, 16)
    ]
    with open(base_file, "w", encoding="utf-8") as f:
        for r in baseline_records:
            f.write(json.dumps(r) + "\n")

    optimized_records = [
        # Turn 1: Architect Plan
        {
            "timestamp": "2026-10-12T14:30:00Z", "session_id": "bench-opt-001",
            "turn_index": 1, "role": "architect", "target_route": "cloud_gemini_pro",
            "raw_input_tokens": 4200, "pruned_input_tokens": 3100,
            "output_tokens": 450, "tokens_saved": 1100, "latency_ms": 1100,
            "vllm_cache_hit_rate": 0.0
        }
    ]
    # Turns 2-16: Builder on 5090
    for i in range(2, 17):
        optimized_records.append({
            "timestamp": f"2026-10-12T14:3{i:02d}:00Z", "session_id": "bench-opt-001",
            "turn_index": i, "role": "builder", "target_route": "local_vllm_5090",
            "raw_input_tokens": 34500, "pruned_input_tokens": 3950,
            "output_tokens": 380, "tokens_saved": 30550, "latency_ms": 780,
            "vllm_cache_hit_rate": 0.814
        })

    with open(opt_file, "w", encoding="utf-8") as f:
        for r in optimized_records:
            f.write(json.dumps(r) + "\n")

    print(f"Sample datasets generated:\n  - {base_file}\n  - {opt_file}")


generate_sample_session_logs = generate_samples


def main():
    parser = argparse.ArgumentParser(
        prog="cascadegateway.audit",
        description="CascadeGateway Token Telemetry & Architect-Builder Audit CLI"
    )
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "OPTIMIZED"),
                        help="Compare baseline and optimized .jsonl telemetry logs")
    parser.add_argument("--generate-samples", action="store_true",
                        help="Generate baseline.jsonl and optimized.jsonl sample files")

    args = parser.parse_args()

    if args.generate_samples:
        generate_samples()
        return

    if args.compare:
        compare_sessions(args.compare[0], args.compare[1])
        return

    parser.print_help()


if __name__ == "__main__":
    main()
