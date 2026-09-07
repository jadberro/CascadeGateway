"""
CascadeGateway Models API
Endpoints for hardware discovery, pulling models, loaded VRAM status, Game Mode (VRAM purge), and model selection.
"""

import json
from typing import Optional, List
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cascadegateway.core.config import config
from cascadegateway.core.hardware import HARDWARE_INFO, get_gpu_telemetry
from cascadegateway.core.router import (
    MODEL_SELECTION_STATE,
    get_available_ollama_models,
    resolve_active_model,
    select_best_local_model,
)

router = APIRouter(tags=["models"])


class PullModelRequest(BaseModel):
    name: str


class UnloadModelRequest(BaseModel):
    model: Optional[str] = None


class ModelSelectionUpdate(BaseModel):
    architect: Optional[str] = None
    builder: Optional[str] = None


@router.get("/api/hardware")
async def get_hardware_endpoint():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    rec_model = HARDWARE_INFO["tier"]["recommended_coding"]
    is_installed = any(rec_model == m or m.startswith(rec_model.split(":")[0]) for m in installed)
    best_active = select_best_local_model(installed)
    return {
        "hardware": HARDWARE_INFO,
        "installed_models": installed,
        "recommended_model": rec_model,
        "is_recommended_installed": is_installed,
        "active_local_model": best_active
    }


@router.post("/api/models/pull")
async def pull_model_endpoint(req: PullModelRequest):
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")

    async def stream_pull():
        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                async with client.stream("POST", f"{ollama_url}/api/pull", json={"name": req.name}) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(stream_pull(), media_type="application/x-ndjson")


@router.get("/api/models/loaded")
async def get_loaded_models():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("models", [])
    except Exception:
        pass
    return []


@router.post("/api/models/unload")
async def unload_models_endpoint(req: Optional[UnloadModelRequest] = None):
    """Evicts loaded models from VRAM (1-click Game Mode / VRAM Purge)."""
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    target_model = req.model if req and req.model and req.model != "all" else None

    loaded_models = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_url}/api/ps")
            if resp.status_code == 200:
                loaded_models = resp.json().get("models", [])
    except Exception as e:
        return {"success": False, "error": f"Failed to query Ollama: {e}"}

    to_unload = []
    if target_model:
        to_unload = [target_model]
    else:
        to_unload = [m.get("name") for m in loaded_models if m.get("name")]

    unloaded = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for m_name in to_unload:
            try:
                res = await client.post(
                    f"{ollama_url}/api/generate",
                    json={"model": m_name, "keep_alive": 0}
                )
                if res.status_code == 200:
                    unloaded.append(m_name)
            except Exception:
                pass

    gpu = get_gpu_telemetry()
    return {
        "success": True,
        "unloaded_models": unloaded,
        "message": f"Successfully unloaded {len(unloaded)} model(s) from VRAM. GPU memory is fully cleared for gaming!",
        "vram_used_gb": gpu.get("vram_used_gb", 0),
        "vram_total_gb": gpu.get("vram_total_gb", 0),
        "vram_percent": gpu.get("vram_percent", 0)
    }


@router.get("/api/models/selection")
async def get_model_selection():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    return {
        "selection": MODEL_SELECTION_STATE,
        "installed_models": installed,
        "resolved": {
            "architect": resolve_active_model("architect", installed),
            "builder": resolve_active_model("builder", installed)
        }
    }


@router.post("/api/models/selection")
async def set_model_selection(req: ModelSelectionUpdate):
    if req.architect is not None:
        MODEL_SELECTION_STATE["architect"] = req.architect
    if req.builder is not None:
        MODEL_SELECTION_STATE["builder"] = req.builder
    return await get_model_selection()
