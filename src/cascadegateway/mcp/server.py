#!/usr/bin/env python3
"""
Zero-dependency, resilient MCP stdio server for RTX 5090 (32GB VRAM).
Communicates via standard JSON-RPC 2.0 over Stdio with Antigravity.
Zero external library dependencies (uses Python standard library only).
"""

import sys
import json
import urllib.request
import urllib.error

GATEWAY_URL = "http://127.0.0.1:8000/v1"
OLLAMA_URL = "http://127.0.0.1:11434"

TOOLS = [
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
    }
]


def query_local_5090(prompt: str, system_prompt: str = "You are an expert coding assistant.") -> str:
    # 1. Try local cascading gateway on port 8000
    try:
        payload = json.dumps({
            "model": "local-5090",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{GATEWAY_URL}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        pass

    # 2. Resilient Direct Fallback to Ollama (Port 11434, qwen2.5-coder:32b in VRAM)
    try:
        payload = json.dumps({
            "model": "qwen2.5-coder:32b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("message", {}).get("content", "")
    except Exception as e:
        return f"Error communicating with local 5090: {e}"


def cascade_llm(prompt: str, system_prompt: str = "You are an expert coding assistant.") -> str:
    try:
        payload = json.dumps({
            "model": "cascade-auto",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.4
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{GATEWAY_URL}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception:
        return query_local_5090(prompt, system_prompt)


def get_cascade_metrics() -> str:
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/metrics")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return "Gateway on port 8000 is idle. Ollama direct is ready on port 11434 with qwen2.5-coder:32b."


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})

        if method == "initialize":
            res = {
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
            continue
        elif method == "ping":
            res = {"jsonrpc": "2.0", "id": req_id, "result": {}}
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }
        elif method == "tools/call":
            t_name = params.get("name")
            t_args = params.get("arguments", {})
            try:
                if t_name == "query_local_5090":
                    text = query_local_5090(
                        t_args.get("prompt", ""),
                        t_args.get("system_prompt", "You are an expert coding assistant.")
                    )
                elif t_name == "cascade_llm":
                    text = cascade_llm(
                        t_args.get("prompt", ""),
                        t_args.get("system_prompt", "You are an expert coding assistant.")
                    )
                elif t_name == "get_cascade_metrics":
                    text = get_cascade_metrics()
                else:
                    text = f"Unknown tool: {t_name}"
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False
                    }
                }
            except Exception as e:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(e)}],
                        "isError": True
                    }
                }
        else:
            if req_id is not None:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }
            else:
                continue

        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
