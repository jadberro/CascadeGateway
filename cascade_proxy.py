"""
CascadeGateway Backward-Compatibility Wrapper
Delegates to modular cascadegateway package.
"""

import sys
from pathlib import Path

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cascadegateway.core.config import (
    config,
    acquire_single_instance_lock,
    cleanup_single_instance_lock,
    BASE_DIR,
    DATA_DIR,
    PID_FILE,
    METRICS_FILE,
)
from cascadegateway.core.hardware import (
    HARDWARE_INFO,
    TIER_PROFILES,
    get_tier_for_vram,
    detect_gpu_hardware,
    match_best_model,
    get_gpu_telemetry,
)
from cascadegateway.core.router import (
    METRICS,
    SERVER_START_TIME,
    BIASING_STATE,
    WORKFLOW_MODES,
    WORKFLOW_STATE,
    MODEL_SELECTION_STATE,
    ARCHITECT_SYSTEM_PROMPT,
    BUILDER_SYSTEM_PROMPT,
    load_persistent_metrics,
    save_persistent_metrics,
    record_request_history,
    calculate_effective_bias,
    evaluate_routing,
    estimate_tokens,
    count_messages_tokens,
    select_architect_model,
    select_best_local_model,
    resolve_active_model,
    get_available_ollama_models,
    call_ollama_non_streaming,
    call_gemini_non_streaming,
)
from cascadegateway.api.server import app, main

if __name__ == "__main__":
    main()
