"""
CascadeGateway Pipeline API
Endpoints for multi-task workflow modes and the Interactive Review Gate (Architect & Builder).
"""

import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cascadegateway.core.config import config
from cascadegateway.core.router import (
    METRICS,
    WORKFLOW_MODES,
    WORKFLOW_STATE,
    ARCHITECT_SYSTEM_PROMPT,
    BUILDER_SYSTEM_PROMPT,
    get_available_ollama_models,
    resolve_active_model,
    call_ollama_non_streaming,
    record_request_history,
)

router = APIRouter(tags=["pipeline"])


class WorkflowModeRequest(BaseModel):
    mode: str


class PipelineArchitectRequest(BaseModel):
    prompt: str
    context: Optional[str] = None
    architect_model: Optional[str] = None


class PipelineRefineRequest(BaseModel):
    prompt: str
    current_blueprint: str
    feedback: str
    architect_model: Optional[str] = None


class PipelineBuildRequest(BaseModel):
    prompt: str
    approved_blueprint: str
    feedback: Optional[str] = None
    builder_model: Optional[str] = None


@router.get("/v1/workflow/modes")
async def get_workflow_modes():
    return {
        "active_mode": WORKFLOW_STATE["active_mode"],
        "modes": WORKFLOW_MODES
    }


@router.post("/v1/workflow/mode")
async def set_workflow_mode(req: WorkflowModeRequest):
    requested = req.mode.lower()
    target_mode = "solo" if requested == "builder" else requested
    if target_mode in WORKFLOW_MODES or requested in WORKFLOW_MODES:
        mode_key = requested if requested in WORKFLOW_MODES else target_mode
        WORKFLOW_STATE["active_mode"] = mode_key
        info = WORKFLOW_MODES.get(mode_key, WORKFLOW_MODES.get(target_mode))
        return {"success": True, "active_mode": mode_key, "info": info}
    raise HTTPException(status_code=400, detail=f"Unknown workflow mode: {req.mode}. Available: {list(WORKFLOW_MODES.keys())}")


@router.post("/api/pipeline/architect")
async def pipeline_architect_endpoint(req: PipelineArchitectRequest):
    """Stage 1: Prompts the Architect model to draft the blueprint for human review."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.architect_model or resolve_active_model("architect", installed)

    user_content = f"TASK SPECIFICATION:\n{req.prompt}"
    if req.context:
        user_content += f"\n\nEXISTING CODEBASE / CONTEXT:\n{req.context}"

    messages = [
        {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.3)
        duration = round(time.time() - t0, 2)
        blueprint_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Architect] {req.prompt}", "Local Architect", model, duration, tokens, 0)

        return {
            "success": True,
            "blueprint": blueprint_text,
            "architect_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Architect phase failed: {e}")


@router.post("/api/pipeline/refine")
async def pipeline_refine_endpoint(req: PipelineRefineRequest):
    """Refines the architectural blueprint with user feedback at the Review Gate."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.architect_model or resolve_active_model("architect", installed)

    refine_prompt = f"""ORIGINAL TASK:
{req.prompt}

CURRENT BLUEPRINT:
{req.current_blueprint}

USER FEEDBACK / ADJUSTMENTS AT REVIEW GATE:
{req.feedback}

Please update the Architectural Blueprint incorporating the user's adjustments. Maintain the structured sections (Architecture, Contracts, Hazards, Plan)."""

    messages = [
        {"role": "system", "content": ARCHITECT_SYSTEM_PROMPT},
        {"role": "user", "content": refine_prompt}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.3)
        duration = round(time.time() - t0, 2)
        updated_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Refine Arch] {req.prompt}", "Local Architect", model, duration, tokens, 0)

        return {
            "success": True,
            "blueprint": updated_text,
            "architect_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refine phase failed: {e}")


@router.post("/api/pipeline/build")
async def pipeline_build_endpoint(req: PipelineBuildRequest):
    """Stage 2: Takes the approved architectural blueprint and generates production code via Builder."""
    t0 = time.time()
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = req.builder_model or resolve_active_model("builder", installed)

    build_prompt = f"""TASK:
{req.prompt}

APPROVED ARCHITECTURAL BLUEPRINT & DATA CONTRACTS:
{req.approved_blueprint}
"""
    if req.feedback:
        build_prompt += f"\nADDITIONAL CONSTRAINTS / ADJUSTMENTS:\n{req.feedback}\n"

    build_prompt += "\nPlease implement the full, production-ready solution with complete code and unit tests."

    messages = [
        {"role": "system", "content": BUILDER_SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt}
    ]

    try:
        resp = await call_ollama_non_streaming(ollama_url, model, messages, temperature=0.2)
        duration = round(time.time() - t0, 2)
        code_text = resp["choices"][0]["message"]["content"]
        tokens = resp["usage"]["prompt_tokens"] + resp["usage"]["completion_tokens"]

        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += resp["usage"]["prompt_tokens"]
        METRICS["tokens_saved_completion"] += resp["usage"]["completion_tokens"]
        record_request_history(f"[Builder] {req.prompt}", "Local Builder", model, duration, tokens, 0)

        return {
            "success": True,
            "code": code_text,
            "builder_model": model,
            "latency_sec": duration,
            "tokens_saved": tokens
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Builder phase failed: {e}")
