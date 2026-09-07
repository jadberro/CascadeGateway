"""
CascadeGateway Core Package
"""

from cascadegateway.core.config import (
    config,
    BASE_DIR,
    DATA_DIR,
    PID_FILE,
    METRICS_FILE,
    acquire_single_instance_lock,
    cleanup_single_instance_lock,
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
from cascadegateway.core.classifier import (
    validate_structure,
    LexicalClassifier,
    CLASSIFIER,
    route_request,
)
from cascadegateway.core.streaming import (
    stream_with_lookahead_failover,
    stream_gemini_fallback,
    make_openai_sse_chunk,
)
