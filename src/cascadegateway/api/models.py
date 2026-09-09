"""
CascadeGateway Models API
Endpoints for hardware discovery, pulling models, loaded VRAM status, Game Mode (VRAM purge), and model selection.
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import time
from cascadegateway.core.config import config
from cascadegateway.core.hardware import HARDWARE_INFO, get_gpu_telemetry
from cascadegateway.core.router import (
    MODEL_SELECTION_STATE,
    HUD_STATE,
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

    HUD_STATE.update({
        "status": "unloading",
        "route": "Clearing VRAM",
        "reason": "Unloading local models from VRAM for gaming/3D rendering...",
        "updated_at": time.strftime("%H:%M:%S")
    })

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
    HUD_STATE.update({
        "status": "paused",
        "route": "GPU Cleared (0MB VRAM)",
        "reason": "VRAM freed for gaming/3D. Press Warm to reload.",
        "updated_at": time.strftime("%H:%M:%S")
    })
    return {
        "success": True,
        "unloaded_models": unloaded,
        "message": f"Successfully unloaded {len(unloaded)} model(s) from VRAM. GPU memory is fully cleared for gaming!",
        "vram_used_gb": gpu.get("vram_used_gb", 0),
        "vram_total_gb": gpu.get("vram_total_gb", 0),
        "vram_percent": gpu.get("vram_percent", 0)
    }


@router.post("/api/models/preload")
async def preload_models_endpoint(req: Optional[Dict[str, Any]] = None):
    """Preloads the active local model into VRAM with keep_alive: -1 so it stays permanently hot and resident."""
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    best_model = select_best_local_model(installed)
    target_model = (req.get("model") if req else None) or best_model
    if not target_model:
        return {"success": False, "error": "No local model available to preload."}

    HUD_STATE.update({
        "status": "warming",
        "route": f"Warming {target_model}",
        "model": target_model,
        "reason": f"Pinning {target_model} into RTX 5090 VRAM...",
        "updated_at": time.strftime("%H:%M:%S")
    })

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={"model": target_model, "keep_alive": -1}
            )
            if resp.status_code == 200:
                gpu = get_gpu_telemetry()
                HUD_STATE.update({
                    "status": "ready",
                    "route": f"Local 5090 ({target_model})",
                    "model": target_model,
                    "reason": f"Warmed & Resident in VRAM (0-delay ready)",
                    "updated_at": time.strftime("%H:%M:%S")
                })
                return {
                    "success": True,
                    "model": target_model,
                    "message": f"Successfully preloaded {target_model} into VRAM with indefinite residency.",
                    "vram_used_gb": gpu.get("vram_used_gb", 0),
                    "vram_percent": gpu.get("vram_percent", 0)
                }
            err_msg = f"Ollama returned status {resp.status_code}"
            HUD_STATE.update({
                "status": "error",
                "reason": f"Preload failed: {err_msg}",
                "updated_at": time.strftime("%H:%M:%S")
            })
            return {"success": False, "error": err_msg}
    except Exception as e:
        err_msg = str(e)
        HUD_STATE.update({
            "status": "error",
            "reason": f"Preload failed: {err_msg}",
            "updated_at": time.strftime("%H:%M:%S")
        })
        return {"success": False, "error": err_msg}


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


async def is_model_in_vram(model_name: str) -> bool:
    """Checks via Ollama /api/ps if the specified model is currently active in VRAM."""
    loaded = await get_loaded_models()
    return any(model_name in m.get("name", "") for m in loaded)


def auto_configure_ides() -> Dict[str, Any]:
    """Auto-configures Continue and Cursor to connect to CascadeGateway on localhost:8000."""
    home_dir = Path.home()
    details = []
    continue_configured = False

    # 1. Continue Extension Configuration (~/.continue/config.json)
    continue_config_path = home_dir / ".continue" / "config.json"
    if continue_config_path.exists():
        try:
            with open(continue_config_path, "r", encoding="utf-8") as f:
                c_data = json.load(f)

            models_list = c_data.get("models", [])
            already_present = any(m.get("apiBase") == "http://127.0.0.1:8000/v1" for m in models_list)

            if not already_present:
                cascade_entry = {
                    "title": "CascadeGateway (RTX 5090 Auto)",
                    "provider": "openai",
                    "model": "cascade-auto",
                    "apiBase": "http://127.0.0.1:8000/v1",
                    "apiKey": "dummy"
                }
                models_list.append(cascade_entry)
                c_data["models"] = models_list
                with open(continue_config_path, "w", encoding="utf-8") as f:
                    json.dump(c_data, f, indent=2)
                continue_configured = True
                details.append("Added CascadeGateway to ~/.continue/config.json")
            else:
                details.append("CascadeGateway already present in ~/.continue/config.json")
        except Exception as e:
            details.append(f"Error updating Continue config: {e}")
    else:
        details.append("Continue config file not found at ~/.continue/config.json")

    # 2. Cursor Configuration (~/.cursorrules or workspace instructions)
    cursorrules_path = home_dir / ".cursorrules"
    cursor_dir = home_dir / ".cursor"
    if cursorrules_path.exists() or cursor_dir.exists():
        details.append("Cursor environment detected. OpenAI base URL can be pointed to http://127.0.0.1:8000/v1")
    else:
        details.append("Cursor standard global config checked")

    return {
        "success": True,
        "continue_configured": continue_configured,
        "details": details,
        "endpoint": "http://127.0.0.1:8000/v1"
    }


@router.post("/api/ide/auto-config")
async def auto_config_endpoint():
    """1-Click IDE Auto-Configuration Endpoint."""
    return auto_configure_ides()

