"""
CascadeGateway 3-Token Lookahead Buffer & Transparent Failover Engine
Guarantees uninterrupted streaming for IDE clients (Cursor, Continue, Aider) by verifying
local health before header flush and transparently escalating to Gemini Cloud on stall.
"""

import time
import json
import uuid
import asyncio
from typing import AsyncGenerator, List, Dict, Any, Optional

import httpx

from cascadegateway.core.config import config
from cascadegateway.core.router import (
    METRICS,
    record_request_history,
    count_messages_tokens,
    save_persistent_metrics
)

TTFT_DEADLINE_LOADED = 2.5    # 2.5s for warm models in VRAM
TTFT_DEADLINE_COLD = 4.0      # 4.0s grace window for NVMe-to-VRAM model loading


def make_openai_sse_chunk(chunk_id: str, model_name: str, content: str, finish_reason: Optional[str] = None) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason
            }
        ]
    }
    return f"data: {json.dumps(payload)}\n\n"


async def stream_gemini_fallback(
    model: str,
    messages: List[Dict[str, Any]],
    api_key: str,
    chunk_id: str,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None
) -> AsyncGenerator[str, None]:
    """Streams completion from Google Gemini formatted as OpenAI SSE chunks."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}"

    contents = []
    sys_inst = None
    for m in messages:
        role = m.get("role")
        text = m.get("content", "")
        text_str = text if isinstance(text, str) else json.dumps(text)
        if role == "system":
            sys_inst = text_str
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text_str}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text_str}]})

    body: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature}
    }
    if max_tokens:
        body["generationConfig"]["maxOutputTokens"] = max_tokens
    if sys_inst:
        body["systemInstruction"] = {"parts": [{"text": sys_inst}]}

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    raw_data = line[6:].strip()
                    if not raw_data:
                        continue
                    try:
                        chunk_json = json.loads(raw_data)
                        candidates = chunk_json.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text_piece = "".join([p.get("text", "") for p in parts])
                            if text_piece:
                                yield make_openai_sse_chunk(chunk_id, f"cloud:{model}", text_piece)
                    except Exception:
                        continue

    yield make_openai_sse_chunk(chunk_id, f"cloud:{model}", "", finish_reason="stop")
    yield "data: [DONE]\n\n"


async def stream_with_lookahead_failover(
    local_url: str,
    local_model: str,
    cloud_model: str,
    gemini_api_key: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    is_loaded: bool = True,
    user_snippet: str = ""
) -> AsyncGenerator[str, None]:
    """
    Phase 3: 3-Token Lookahead Buffer & Transparent Failover Engine.
    Intercepts the first 3 tokens in memory. If local stalls or fails,
    transparently switches to Gemini Cloud before the client receives broken headers.
    """
    t0 = time.time()
    chunk_id = f"chatcmpl-cascade-{uuid.uuid4().hex[:12]}"
    ttft_deadline = TTFT_DEADLINE_LOADED if is_loaded else TTFT_DEADLINE_COLD
    timeout = config.get("local", {}).get("timeout_seconds", 120)

    ollama_payload = {
        "model": local_model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_ctx": 32768}
    }
    if max_tokens:
        ollama_payload["options"]["num_predict"] = max_tokens

    # Step 1: Handshake & 3-Token Lookahead Queue
    token_queue: asyncio.Queue = asyncio.Queue()
    producer_error = asyncio.Event()
    error_container = {}

    async def local_producer(client: httpx.AsyncClient):
        try:
            async with client.stream("POST", f"{local_url}/api/chat", json=ollama_payload) as resp:
                if resp.status_code != 200:
                    error_container["error"] = f"HTTP {resp.status_code}"
                    producer_error.set()
                    return

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("done", False):
                            await token_queue.put(None)  # Sentinel
                            break
                        content = data.get("message", {}).get("content", "")
                        if content:
                            await token_queue.put(content)
                    except Exception as parse_err:
                        error_container["error"] = str(parse_err)
                        producer_error.set()
                        return
        except Exception as e:
            error_container["error"] = str(e)
            producer_error.set()

    client = httpx.AsyncClient(timeout=timeout)
    producer_task = asyncio.create_task(local_producer(client))

    # Intercept first 3 tokens into lookahead buffer
    lookahead_buffer: List[str] = []
    local_verified = False

    try:
        while len(lookahead_buffer) < 3:
            remaining_time = max(0.1, ttft_deadline - (time.time() - t0))
            if producer_error.is_set():
                break

            try:
                item = await asyncio.wait_for(token_queue.get(), timeout=remaining_time)
                if item is None:  # Model finished early (e.g. 1-2 tokens)
                    local_verified = True
                    break
                lookahead_buffer.append(item)
                if len(lookahead_buffer) >= 3:
                    local_verified = True
                    break
            except asyncio.TimeoutError:
                break
    except Exception:
        pass

    # Scenario B: Local engine failed or stalled before health verification
    if not local_verified or producer_error.is_set():
        producer_task.cancel()
        await client.aclose()
        lookahead_buffer.clear()

        METRICS["cloud_fallbacks"] += 1
        save_persistent_metrics()

        # Transparently replay to Google Gemini Cloud
        if gemini_api_key:
            async for chunk in stream_gemini_fallback(
                cloud_model, messages, gemini_api_key, chunk_id,
                temperature=temperature, max_tokens=max_tokens
            ):
                yield chunk
            return
        else:
            yield make_openai_sse_chunk(
                chunk_id, "local:failed",
                "[Gateway Notice: Local GPU stalled and GEMINI_API_KEY is not configured for cloud fallback.]",
                finish_reason="stop"
            )
            yield "data: [DONE]\n\n"
            return

    # Scenario A: Local Engine Verified! Flush headers & buffered tokens
    METRICS["local_5090_requests"] += 1
    prompt_toks = count_messages_tokens(messages)
    METRICS["tokens_saved_prompt"] += prompt_toks

    total_tokens_emitted = len(lookahead_buffer)
    for token_text in lookahead_buffer:
        yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", token_text)

    # Stream remaining local tokens directly through
    try:
        while True:
            item = await token_queue.get()
            if item is None:
                break
            total_tokens_emitted += 1
            yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", item)
    except Exception as stream_err:
        # Scenario C: Post-Handshake Mid-Stream Stall
        # Do not splice cloud output. Cleanly close with synthetic notice.
        yield make_openai_sse_chunk(
            chunk_id, f"local:{local_model}",
            f"\n\n[Gateway Alert: Local GPU stream stalled mid-generation ({stream_err})]"
        )

    yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", "", finish_reason="stop")
    yield "data: [DONE]\n\n"

    duration = time.time() - t0
    METRICS["tokens_saved_completion"] += total_tokens_emitted
    record_request_history(user_snippet, "Local GPU (Lookahead Verified)", local_model, duration, prompt_toks + total_tokens_emitted, 0)

    producer_task.cancel()
    await client.aclose()
