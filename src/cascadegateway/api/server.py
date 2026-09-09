"""
CascadeGateway FastAPI Server
Main entry point orchestrating routers, static assets, templates, /v1/chat/completions, and MCP endpoints.
"""

import os
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cascadegateway.core.config import config, acquire_single_instance_lock
from cascadegateway.core.hardware import HARDWARE_INFO, get_gpu_telemetry
from cascadegateway.core.router import (
    METRICS,
    SERVER_START_TIME,
    BIASING_STATE,
    WORKFLOW_MODES,
    WORKFLOW_STATE,
    MODEL_SELECTION_STATE,
    save_persistent_metrics,
    record_request_history,
    calculate_effective_bias,
    evaluate_routing,
    count_messages_tokens,
    select_best_local_model,
    resolve_active_model,
    get_available_ollama_models,
    call_ollama_non_streaming,
    call_gemini_non_streaming,
    HUD_STATE,
    get_estimated_dollars_saved,
)
from cascadegateway.core.classifier import route_request
from cascadegateway.api import models, pipeline

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(
    title="CascadeGateway",
    description="Hardware-Adaptive Local LLM & Cloud Cascading Architecture",
    version="2.0.0",
)





app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(models.router)
app.include_router(pipeline.router)


# -------------------------------------------------------------
# Web Dashboard
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    gpu = get_gpu_telemetry()
    reqs = {
        "total": METRICS["total_requests"],
        "local": METRICS["local_5090_requests"],
        "cloud": METRICS["cloud_gemini_requests"],
        "fallbacks": METRICS["cloud_fallbacks"]
    }
    pct = 0.0
    if reqs["total"] > 0:
        pct = round((reqs["local"] / reqs["total"]) * 100, 1)

    saved_toks = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    saved_usd = (saved_toks / 1_000_000) * 2.50

    eff_bias, routing_thresh, bias_desc = calculate_effective_bias()
    eff_bias_pct = int(round(eff_bias * 100))

    bias = {
        "mode": BIASING_STATE["mode"],
        "bias_factor": BIASING_STATE["bias_factor"],
        "effective_bias": eff_bias,
        "description": bias_desc
    }

    tier = HARDWARE_INFO["tier"]
    gpu_title = HARDWARE_INFO["gpu_name"]
    rec_model = tier["recommended_coding"]
    active_wf_mode = WORKFLOW_STATE["active_mode"]
    wf_info = WORKFLOW_MODES.get(active_wf_mode, WORKFLOW_MODES["architect"])
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed_models = await get_available_ollama_models(ollama_url)
    resolved_arch = resolve_active_model("architect", installed_models)
    resolved_build = resolve_active_model("builder", installed_models)

    context = {
        "request": request,
        "gpu": gpu,
        "gpu_title": gpu_title,
        "tier": tier,
        "rec_model": rec_model,
        "saved_toks": saved_toks,
        "saved_usd": saved_usd,
        "pct": pct,
        "reqs": reqs,
        "bias": bias,
        "eff_bias_pct": eff_bias_pct,
        "active_wf_mode": active_wf_mode,
        "wf_info": wf_info,
        "resolved_arch": resolved_arch,
        "resolved_build": resolved_build,
        "recent": METRICS.get("recent_requests", [])[:10],
    }

    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


# -------------------------------------------------------------
# OpenAI Compatible Endpoints
# -------------------------------------------------------------
@app.get("/v1/models")
async def list_models():
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    local_models = await get_available_ollama_models(ollama_url)
    model_list = [
        {"id": "cascade-auto", "object": "model", "owned_by": "cascading-gateway", "description": "Auto-routing (Local GPU -> Gemini)"},
        {"id": "local-5090", "object": "model", "owned_by": "local-gpu", "description": "Forces local execution on GPU ($0 tokens)"},
        {"id": "gemini-2.5-flash", "object": "model", "owned_by": "google", "description": "Google Gemini 2.5 Flash"},
        {"id": "gemini-2.5-pro", "object": "model", "owned_by": "google", "description": "Google Gemini 2.5 Pro"},
    ]
    for m in local_models:
        model_list.append({"id": m, "object": "model", "owned_by": "ollama", "description": f"Local model: {m}"})
    return {"object": "list", "data": model_list}


class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    model: Optional[str] = "cascade-auto"
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    functions: Optional[List[Dict[str, Any]]] = None


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    global LAST_REQUEST_TIME
    LAST_REQUEST_TIME = time.time()
    t0 = time.time()
    METRICS["total_requests"] += 1
    messages = req.messages
    req_model = req.model or "cascade-auto"

    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    available_local_models = await get_available_ollama_models(ollama_url)
    best_architect_model = resolve_active_model("architect", available_local_models)
    best_builder_model = resolve_active_model("builder", available_local_models)

    # Execute Phase 1 (Structural & Hardware Validation) and Phase 2 (Ultra-Fast Lexical Scan) <5ms
    decision = route_request(
        messages=messages,
        tools=req.tools or req.functions,
        requested_model=req_model,
        hardware_info=HARDWARE_INFO,
        biasing_state=BIASING_STATE
    )

    route_target = decision["route"]
    route_reason = decision["reason"]
    comp_score = decision["complexity_score"]
    prompt_tokens = count_messages_tokens(messages)
    api_key = os.getenv("GEMINI_API_KEY") or ""

    user_snippet = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            user_snippet = content if isinstance(content, str) else json.dumps(content)
            break

    # Determine Cloud Model Fallback
    cloud_model = config.get("cloud", {}).get("default_model", "gemini-2.5-flash")
    if comp_score > 0.85:
        cloud_model = config.get("cloud", {}).get("pro_model", "gemini-2.5-pro")

    target_role = decision.get("target_role", "builder")
    active_wf = WORKFLOW_STATE.get("active_mode", "architect")
    if active_wf in ("solo", "builder"):
        target_local_model = best_builder_model
        route_display = "local_builder"
    elif active_wf == "algo" or target_role == "architect" or "architect" in route_target:
        target_local_model = best_architect_model
        route_display = "local_architect"
    else:
        target_local_model = best_builder_model
        route_display = "local_builder"

    is_local_route = route_target.startswith("local") or route_target in ("local", "local_builder", "local_architect")
    chosen_model = target_local_model if is_local_route else cloud_model

    HUD_STATE.update({
        "status": "streaming" if req.stream else "processing",
        "route": f"Local Architect ({target_local_model})" if target_role == "architect" else f"Local Builder ({target_local_model})" if is_local_route else "Gemini Cloud",
        "model": chosen_model,
        "route_time": f"{round(decision['latency_ms'], 3)}ms",
        "reason": str(route_reason),
        "last_query": user_snippet[:120] if user_snippet else "Empty prompt",
        "updated_at": time.strftime("%H:%M:%S")
    })

    # Diagnostic Routing Headers
    routing_headers = {
        "X-Cascade-Route": route_display if is_local_route else "cloud",
        "X-Cascade-Model": chosen_model,
        "X-Cascade-Decision-MS": str(round(decision["latency_ms"], 3)),
        "X-Cascade-Reason": str(route_reason)
    }

    # ASYMMETRIC VERIFICATION TOPOLOGY: Local Generator + Cloud Critic
    is_verify_mode = (
        WORKFLOW_STATE.get("active_mode") == "verify" or
        user_snippet.strip().startswith("/verify") or
        user_snippet.strip().startswith("/critique") or
        "[verify]" in user_snippet.lower()
    )
    if is_verify_mode and available_local_models:
        from cascadegateway.core.streaming import stream_asymmetric_verification
        # Clean prompt tag from message for inference
        cleaned_messages = [dict(m) for m in messages]
        for m in reversed(cleaned_messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                c = m["content"]
                for tag in ("/verify", "/critique"):
                    if c.strip().startswith(tag):
                        m["content"] = c.strip()[len(tag):].strip()
                break

        verify_headers = {
            "X-Cascade-Route": "asymmetric-verification",
            "X-Cascade-Model": f"{target_local_model}->{cloud_model}",
            "X-Cascade-Decision-MS": str(round(decision["latency_ms"], 3)),
            "X-Cascade-Reason": "Asymmetric Consensus (Local Generator + Cloud Critic)",
            "X-Cascade-Pipeline": "asymmetric-verification"
        }
        if req.stream:
            generator = stream_asymmetric_verification(
                local_url=ollama_url,
                local_model=target_local_model,
                cloud_model=cloud_model,
                gemini_api_key=api_key,
                messages=cleaned_messages,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens,
                user_snippet=user_snippet
            )
            return StreamingResponse(generator, media_type="text/event-stream", headers=verify_headers)

    # PHASE 3: STREAMING EXECUTION WITH 3-TOKEN LOOKAHEAD BUFFER & FAILOVER
    if req.stream:
        if is_local_route and available_local_models:
            from cascadegateway.core.streaming import stream_with_lookahead_failover
            # Check if model is currently warm in VRAM
            from cascadegateway.api.models import get_loaded_models
            try:
                loaded = await get_loaded_models()
                is_loaded = any(target_local_model in m.get("name", "") for m in loaded)
            except Exception:
                is_loaded = True

            generator = stream_with_lookahead_failover(
                local_url=ollama_url,
                local_model=target_local_model,
                cloud_model=cloud_model,
                gemini_api_key=api_key,
                messages=messages,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens,
                is_loaded=is_loaded,
                user_snippet=user_snippet
            )
            return StreamingResponse(generator, media_type="text/event-stream", headers=routing_headers)

        # Directly routed to Cloud (Streamed)
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail=f"Query escalated to Cloud ({route_reason}), but GEMINI_API_KEY is not configured in .env."
            )
        from cascadegateway.core.streaming import stream_gemini_fallback
        import uuid
        chunk_id = f"chatcmpl-cloud-{uuid.uuid4().hex[:12]}"
        METRICS["cloud_gemini_requests"] += 1
        save_persistent_metrics()
        return StreamingResponse(
            stream_gemini_fallback(cloud_model, messages, api_key, chunk_id, req.temperature or 0.7, req.max_tokens),
            media_type="text/event-stream",
            headers=routing_headers
        )

    # NON-STREAMING EXECUTION WITH LOOKAHEAD FAILOVER
    if is_local_route and available_local_models:
        try:
            resp = await call_ollama_non_streaming(
                ollama_url, target_local_model, messages,
                temperature=req.temperature, max_tokens=req.max_tokens
            )
            duration = time.time() - t0
            comp_tokens = resp.get("usage", {}).get("completion_tokens", 0)
            METRICS["local_5090_requests"] += 1
            METRICS["tokens_saved_prompt"] += prompt_tokens
            METRICS["tokens_saved_completion"] += comp_tokens
            record_request_history(user_snippet, f"Local GPU ({route_display})", target_local_model, duration, prompt_tokens + comp_tokens, 0)
            resp["_routing_info"] = {
                "tier": f"Local GPU ({route_display})",
                "reason": route_reason,
                "model": target_local_model,
                "tokens_saved": prompt_tokens + comp_tokens,
                "routing_latency_ms": decision["latency_ms"]
            }
            return JSONResponse(content=resp, headers=routing_headers)
        except Exception as e:
            print(f"[CASCADING NOTICE] Local generation failed ({e}). Replaying to Google Gemini Cloud...")
            METRICS["cloud_fallbacks"] += 1

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=f"Query routed/cascaded to Cloud ({route_reason}), but GEMINI_API_KEY is not configured in .env."
        )

    resp = await call_gemini_non_streaming(cloud_model, messages, api_key, temperature=req.temperature, max_tokens=req.max_tokens)
    duration = time.time() - t0
    comp_tokens = resp.get("usage", {}).get("completion_tokens", 0)
    METRICS["cloud_gemini_requests"] += 1
    METRICS["cloud_tokens_prompt"] += prompt_tokens
    METRICS["cloud_tokens_completion"] += comp_tokens
    record_request_history(user_snippet, "Google Gemini Cloud", cloud_model, duration, 0, prompt_tokens + comp_tokens)
    resp["_routing_info"] = {
        "tier": "Google Gemini Cloud",
        "reason": route_reason,
        "model": cloud_model,
        "routing_latency_ms": decision["latency_ms"]
    }
    return JSONResponse(content=resp, headers=routing_headers)


class BiasingUpdateRequest(BaseModel):
    mode: Optional[str] = None
    bias_factor: Optional[float] = None
    cloud_token_budget: Optional[int] = None


@app.get("/v1/settings")
async def get_settings():
    eff_bias, routing_thresh, desc = calculate_effective_bias()
    cloud_tokens = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    budget = BIASING_STATE["cloud_token_budget"]
    pct_used = min(100.0, (cloud_tokens / budget) * 100.0) if budget > 0 else 0.0
    return {
        "biasing": {
            "mode": BIASING_STATE["mode"],
            "bias_factor": BIASING_STATE["bias_factor"],
            "effective_bias": round(eff_bias, 3),
            "routing_threshold": round(routing_thresh, 3),
            "description": desc,
            "cloud_token_budget": budget,
            "cloud_tokens_consumed": cloud_tokens,
            "budget_consumed_percentage": round(pct_used, 1),
        }
    }


@app.post("/v1/settings")
async def update_settings(update: BiasingUpdateRequest):
    if update.mode is not None:
        BIASING_STATE["mode"] = update.mode
    if update.bias_factor is not None:
        BIASING_STATE["bias_factor"] = min(1.0, max(0.0, update.bias_factor))
    if update.cloud_token_budget is not None:
        BIASING_STATE["cloud_token_budget"] = max(1000, update.cloud_token_budget)
    return await get_settings()


@app.post("/v1/settings/reset-budget")
async def reset_budget():
    METRICS["cloud_tokens_prompt"] = 0
    METRICS["cloud_tokens_completion"] = 0
    save_persistent_metrics()
    return {"message": "Cloud token consumption reset", "settings": await get_settings()}


@app.get("/api/config-templates")
async def get_config_templates():
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8000)
    base_url = f"http://{host}:{port}/v1"
    mcp_url = f"http://{host}:{port}/mcp"

    return {
        "antigravity": {
            "file": "~/.gemini/config/mcp_config.json",
            "content": {
                "mcpServers": {
                    "rtx5090-cascade": {
                        "serverUrl": mcp_url
                    }
                }
            }
        },
        "cursor": {
            "description": "Cursor Settings > Models > OpenAI API Key",
            "baseURL": base_url,
            "apiKey": "cascading-local",
            "model": "cascade-auto"
        },
        "continue": {
            "file": "~/.continue/config.json",
            "snippet": {
                "models": [
                    {
                        "title": f"Local Cascade ({HARDWARE_INFO['gpu_name']})",
                        "provider": "openai",
                        "model": "cascade-auto",
                        "apiBase": base_url,
                        "apiKey": "cascading-local"
                    }
                ]
            }
        },
        "python_sdk": f"""from openai import OpenAI

client = OpenAI(
    base_url="{base_url}",
    api_key="cascading-local"
)

response = client.chat.completions.create(
    model="cascade-auto",
    messages=[{{"role": "user", "content": "Write a Python function to check prime numbers."}}]
)
print(response.choices[0].message.content)"""
    }


@app.get("/metrics")
@app.get("/v1/metrics")
async def get_metrics():
    uptime = time.time() - SERVER_START_TIME
    total_tokens_saved = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    cloud_tokens_total = METRICS["cloud_tokens_prompt"] + METRICS["cloud_tokens_completion"]
    offload_percent = 0.0
    if METRICS["total_requests"] > 0:
        offload_percent = (METRICS["local_5090_requests"] / METRICS["total_requests"]) * 100.0

    eff_bias, routing_thresh, bias_desc = calculate_effective_bias()

    return {
        "uptime_seconds": int(uptime),
        "requests": {
            "total": METRICS["total_requests"],
            "local_5090": METRICS["local_5090_requests"],
            "cloud_gemini": METRICS["cloud_gemini_requests"],
            "local_offload_percentage": round(offload_percent, 1),
        },
        "tokens": {
            "tokens_saved_locally": total_tokens_saved,
            "cloud_tokens_consumed": cloud_tokens_total,
        },
        "savings": {
            "estimated_dollars_saved": get_estimated_dollars_saved(),
            "cost_basis": "$3.00 per 1M tokens"
        },
        "gpu": get_gpu_telemetry(),
        "biasing": {
            "mode": BIASING_STATE["mode"],
            "effective_bias": round(eff_bias, 3),
            "routing_threshold": round(routing_thresh, 3),
            "description": bias_desc,
        },
        "recent_requests": METRICS.get("recent_requests", [])[:10],
    }


@app.get("/api/hud/state")
async def get_hud_state_endpoint():
    gpu = get_gpu_telemetry()
    dollars = get_estimated_dollars_saved()
    reqs = METRICS["total_requests"]
    offload = round((METRICS["local_5090_requests"] / reqs) * 100, 1) if reqs > 0 else 100.0
    tokens_saved = METRICS["tokens_saved_prompt"] + METRICS["tokens_saved_completion"]
    return {
        "status": HUD_STATE.get("status", "ready"),
        "route": HUD_STATE.get("route", "Local GPU"),
        "model": HUD_STATE.get("model", "qwen2.5-coder:32b"),
        "route_time": HUD_STATE.get("route_time", "0.00ms"),
        "reason": HUD_STATE.get("reason", "Ready"),
        "last_query": HUD_STATE.get("last_query", "Awaiting query..."),
        "updated_at": HUD_STATE.get("updated_at", ""),
        "vram": {
            "used": gpu.get("vram_used_gb", 0.0),
            "total": gpu.get("vram_total_gb", 31.8),
            "percent": gpu.get("vram_percent", 0),
            "temp": gpu.get("temperature_c", 0),
            "power": gpu.get("power_draw_w", 0),
            "name": gpu.get("name", "NVIDIA GeForce RTX 5090")
        },
        "savings": {
            "dollars": dollars,
            "tokens": tokens_saved // 1000,
            "tokens_raw": tokens_saved,
            "cost_basis": "$3.00 per 1M tokens"
        },
        "metrics": {
            "total_requests": reqs,
            "local_requests": METRICS["local_5090_requests"],
            "cloud_requests": METRICS["cloud_gemini_requests"],
            "offload_percent": offload
        },
        "workflow": {
            "active_mode": WORKFLOW_STATE.get("active_mode", "builder"),
            "architect_model": MODEL_SELECTION_STATE.get("architect", "auto"),
            "builder_model": MODEL_SELECTION_STATE.get("builder", "auto")
        }
    }


# -------------------------------------------------------------
# MCP Streamable HTTP Handlers
# -------------------------------------------------------------
MCP_TOOL_DEFS = [
    {
        "name": "query_local_5090",
        "description": "Run a query or code task directly on the local NVIDIA RTX 5090 (qwen2.5-coder:32b) at $0 token cost.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt or coding task to execute on the RTX 5090."},
                "system_prompt": {"type": "string", "description": "Optional system prompt.", "default": "You are an expert coding assistant."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "cascade_llm",
        "description": "Run a query through the intelligent cascading gateway (RTX 5090 -> Gemini) with dynamic biasing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The prompt to process."},
                "system_prompt": {"type": "string", "description": "Optional system prompt.", "default": "You are an expert coding assistant."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "get_cascade_metrics",
        "description": "Get live metrics on tokens saved locally on the RTX 5090, local offload percentage, and biasing state.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "architect_proposal",
        "description": "Generate a system architectural blueprint, component design, interface signatures, and edge-case hazards for human review before writing code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The feature or task specification to architect."},
                "context": {"type": "string", "description": "Optional context or existing file code.", "default": ""}
            },
            "required": ["task"]
        }
    }
]


async def execute_mcp_tool(name: str, args: Dict[str, Any]) -> str:
    ollama_url = config.get("local", {}).get("base_url", "http://127.0.0.1:11434")
    installed = await get_available_ollama_models(ollama_url)
    model = resolve_active_model("builder", installed)

    if name == "query_local_5090":
        prompt = args.get("prompt", "")
        sys_prompt = args.get("system_prompt", "You are an expert coding assistant.")
        msgs = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]
        resp = await call_ollama_non_streaming(ollama_url, model, msgs)
        return resp["choices"][0]["message"]["content"]
    elif name == "cascade_llm":
        prompt = args.get("prompt", "")
        sys_prompt = args.get("system_prompt", "You are an expert coding assistant.")
        req = ChatRequest(messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}])
        res = await chat_completions(req)
        body = json.loads(res.body.decode())
        return body["choices"][0]["message"]["content"]
    elif name == "get_cascade_metrics":
        m = await get_metrics()
        return json.dumps(m, indent=2)
    elif name == "architect_proposal":
        from cascadegateway.api.pipeline import pipeline_architect_endpoint, PipelineArchitectRequest
        res = await pipeline_architect_endpoint(PipelineArchitectRequest(prompt=args.get("task", ""), context=args.get("context")))
        return res["blueprint"]
    return f"Unknown tool: {name}"


@app.get("/mcp")
@app.get("/mcp/sse")
async def mcp_get_probe():
    return {
        "jsonrpc": "2.0",
        "result": {
            "server": "rtx5090-cascade",
            "status": "online",
            "transport": "streamable-http",
            "protocolVersion": "2024-11-05"
        }
    }


@app.post("/mcp")
@app.post("/mcp/sse")
async def mcp_post_handler(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "rtx5090-cascade",
                    "version": "2.0.0"
                }
            }
        }
    elif method == "notifications/initialized":
        return JSONResponse({"jsonrpc": "2.0", "result": None, "id": req_id})
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": MCP_TOOL_DEFS
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            output_text = await execute_mcp_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": output_text}
                    ]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Tool execution failed: {str(e)}"
                }
            }

    return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": req_id}


def main():
    acquire_single_instance_lock()
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8000)
    print(f"Starting Model Cascading Gateway on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
