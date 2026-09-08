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

import collections
import httpx

from cascadegateway.core.config import config
from cascadegateway.core.router import (
    METRICS,
    record_request_history,
    count_messages_tokens,
    save_persistent_metrics
)

TTFT_DEADLINE_LOADED = 8.0    # 8.0s for warm models in VRAM (allows CoT/reasoning models like DeepSeek-R1/Gemma to formulate initial tokens)
TTFT_DEADLINE_COLD = 60.0     # 60.0s grace window for NVMe-to-VRAM model loading when waking up from pause


def detect_repetition_loop(tokens: List[str]) -> bool:
    """
    Sliding-window loop detector tracking repeated sequence patterns of length 2, 3, or 4
    that repeat at least 4 times consecutively.
    """
    for L in (2, 3, 4):
        if len(tokens) >= 4 * L:
            pattern = tokens[-L:]
            tail = tokens[-4 * L:]
            if tail == pattern * 4:
                return True
    return False


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
        "keep_alive": -1,  # Keep permanently resident in GPU VRAM (never unload on idle)
        "options": {"temperature": temperature, "num_ctx": 32768}
    }
    if max_tokens:
        ollama_payload["options"]["num_predict"] = max_tokens

    # Step 1: Handshake & 3-Token Lookahead Queue
    token_queue: asyncio.Queue = asyncio.Queue()
    producer_error = asyncio.Event()
    error_container = {}
    hardware_eval_stats: Dict[str, Any] = {}

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
                            if "eval_count" in data:
                                hardware_eval_stats["eval_count"] = data["eval_count"]
                            if "prompt_eval_count" in data:
                                hardware_eval_stats["prompt_eval_count"] = data["prompt_eval_count"]
                            await token_queue.put(None)  # Sentinel
                            break
                        msg = data.get("message", {})
                        content = msg.get("content", "")
                        thinking = msg.get("thinking", "")
                        token_piece = content if content else thinking
                        if token_piece:
                            await token_queue.put(token_piece)
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
                    # CRITICAL: Re-queue the sentinel so the downstream consumer loop terminates cleanly
                    await token_queue.put(None)
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

    total_tokens_emitted = len(lookahead_buffer)
    recent_tokens = collections.deque(maxlen=16)
    for tok in lookahead_buffer:
        recent_tokens.append(tok)

    try:
        for token_text in lookahead_buffer:
            yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", token_text)

        # Stream remaining local tokens directly through
        while True:
            item = await token_queue.get()
            if item is None:
                break
            total_tokens_emitted += 1
            recent_tokens.append(item)

            if detect_repetition_loop(list(recent_tokens)):
                yield make_openai_sse_chunk(
                    chunk_id, f"local:{local_model}",
                    "\n\n[Gateway Notice: Runaway repetition loop detected and terminated]"
                )
                break

            yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", item)

        yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", "", finish_reason="stop")
        yield "data: [DONE]\n\n"

        duration = time.time() - t0
        # Exact Hardware Token Metrics (eval_count)
        actual_comp_tokens = hardware_eval_stats.get("eval_count", total_tokens_emitted)
        actual_prompt_tokens = hardware_eval_stats.get("prompt_eval_count", prompt_toks)
        METRICS["tokens_saved_prompt"] += actual_prompt_tokens
        METRICS["tokens_saved_completion"] += actual_comp_tokens
        save_persistent_metrics()
        record_request_history(
            user_snippet, "Local GPU (Lookahead Verified)",
            local_model, duration, actual_prompt_tokens + actual_comp_tokens, 0
        )
    except Exception as stream_err:
        # Scenario C: Post-Handshake Mid-Stream Stall
        # Do not splice cloud output. Cleanly close with synthetic notice.
        yield make_openai_sse_chunk(
            chunk_id, f"local:{local_model}",
            f"\n\n[Gateway Alert: Local GPU stream stalled mid-generation ({stream_err})]"
        )
        yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", "", finish_reason="stop")
        yield "data: [DONE]\n\n"
    finally:
        # Guarantees background task and HTTP client are cleaned up on client disconnect
        if not producer_task.done():
            producer_task.cancel()
        await client.aclose()


async def stream_asymmetric_verification(
    local_url: str,
    local_model: str,
    cloud_model: str,
    gemini_api_key: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    user_snippet: str = ""
) -> AsyncGenerator[str, None]:
    """
    Topology A: Asymmetric Consensus (Local Generator + Cloud Critic).
    Local GPU drafts candidate code ($0 cost).
    Cloud Gemini audits the draft for race conditions, memory leaks, type safety.
    """
    t0 = time.time()
    chunk_id = f"chatcmpl-verify-{uuid.uuid4().hex[:12]}"
    prompt_toks = count_messages_tokens(messages)

    # Step 1: Draft candidate solution locally on GPU ($0 cost)
    from cascadegateway.core.router import call_ollama_non_streaming
    try:
        draft_resp = await call_ollama_non_streaming(
            local_url, local_model, messages,
            temperature=temperature, max_tokens=max_tokens
        )
        local_draft = draft_resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        draft_usage = draft_resp.get("usage", {})
        draft_comp_tokens = draft_usage.get("completion_tokens", len(local_draft.split()))
        METRICS["local_5090_requests"] += 1
        METRICS["tokens_saved_prompt"] += prompt_toks
        METRICS["tokens_saved_completion"] += draft_comp_tokens
    except Exception as e:
        local_draft = f"[Local drafting failed: {e}]"
        draft_comp_tokens = 0

    # Step 2: Anonymized Audit Prompt for Cloud Critic
    user_spec = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            user_spec = content if isinstance(content, str) else json.dumps(content)
            break

    audit_messages = [
        {
            "role": "system",
            "content": (
                "You are a Principal Software Auditor. A junior developer wrote this draft implementation "
                "for the provided specification. Perform a strict code audit for race conditions, type safety, "
                "memory management, and correctness. If the draft is clean and correct, output it. If bugs or flaws "
                "are found, output the corrected and polished implementation. Maintain the same code structure "
                "and do not mention being a reviewer."
            )
        },
        {
            "role": "user",
            "content": f"Specification:\n{user_spec}\n\nCandidate Implementation Draft:\n{local_draft}"
        }
    ]

    # Step 3: Stream verified output from Gemini Cloud (or local draft if API key not configured)
    if not gemini_api_key:
        yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", local_draft)
        yield make_openai_sse_chunk(
            chunk_id, f"local:{local_model}",
            "\n\n[Gateway Notice: Cloud critic audit skipped because GEMINI_API_KEY is not configured. Outputting local candidate draft directly.]"
        )
        yield make_openai_sse_chunk(chunk_id, f"local:{local_model}", "", finish_reason="stop")
        yield "data: [DONE]\n\n"
        duration = time.time() - t0
        record_request_history(
            user_snippet, "Asymmetric Verification (Local Draft Only)",
            local_model, duration, prompt_toks + draft_comp_tokens, 0
        )
        return

    METRICS["cloud_gemini_requests"] += 1
    save_persistent_metrics()

    cloud_tokens_emitted = 0
    async for chunk in stream_gemini_fallback(
        cloud_model, audit_messages, gemini_api_key, chunk_id,
        temperature=temperature, max_tokens=max_tokens
    ):
        cloud_tokens_emitted += 1
        yield chunk

    duration = time.time() - t0
    METRICS["cloud_tokens_completion"] += cloud_tokens_emitted
    record_request_history(
        user_snippet, "Asymmetric Verification (5090+Gemini)",
        f"{local_model} -> {cloud_model}", duration,
        prompt_toks + draft_comp_tokens, cloud_tokens_emitted
    )
