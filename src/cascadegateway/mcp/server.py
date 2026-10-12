#!/usr/bin/env python3
"""
Zero-dependency, resilient MCP stdio server for RTX 5090 (32GB VRAM).
Communicates via standard JSON-RPC 2.0 over Stdio with Antigravity.
Zero external library dependencies (uses Python standard library only).
"""

import os
import sys
import re
import ast
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

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
    },
    {
        "name": "generate_and_write_file_5090",
        "description": "Generates code on the local RTX 5090 (32GB VRAM) and writes it directly to disk at $0 cloud token cost. Returns only a compact 25-token status summary without polluting cloud context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute destination path for the file to create or overwrite."},
                "prompt": {"type": "string", "description": "Detailed specification of the code to generate."},
                "system_prompt": {"type": "string", "description": "Optional system prompt.", "default": "You are an expert programming assistant. Output ONLY the code enclosed in triple backticks."},
                "overwrite": {"type": "boolean", "description": "Whether to overwrite existing file if present.", "default": True}
            },
            "required": ["file_path", "prompt"]
        }
    },
    {
        "name": "patch_file_5090",
        "description": "Applies a targeted edit or refactoring to an existing file directly on the local RTX 5090 at $0 cloud token cost, returning only a compact status summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the existing file to modify."},
                "instruction": {"type": "string", "description": "Clear instruction detailing what to modify or add."},
                "search_block": {"type": "string", "description": "Optional specific code snippet within the file to replace."}
            },
            "required": ["file_path", "instruction"]
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


def extract_code_block(raw_text: str, file_path: str) -> str:
    """Extracts code block from markdown triple backticks if present, or returns stripped text."""
    pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    matches = pattern.findall(raw_text)
    if matches:
        return max(matches, key=len).strip()
    return raw_text.strip()


def verify_syntax(code: str, file_path: str) -> Tuple[bool, Optional[str]]:
    """Checks syntax for Python or JSON files."""
    ext = Path(file_path).suffix.lower()
    if ext == ".py":
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}: {e.msg}"
    elif ext in (".json", ".jsonl"):
        try:
            json.loads(code)
            return True, None
        except Exception as e:
            return False, str(e)
    return True, None


def generate_and_write_file_5090(
    file_path: str,
    prompt: str,
    system_prompt: str = "You are an expert programming assistant. Output ONLY the code enclosed in triple backticks. Do not include conversational commentary.",
    overwrite: bool = True
) -> str:
    """
    Direct-to-Disk Zero-Token Execution:
    Queries the local RTX 5090, writes code directly to disk, and returns a 25-token status summary.
    """
    target = Path(file_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite and target.exists():
        return json.dumps({
            "status": "error",
            "error": f"File {target} already exists and overwrite is False.",
            "file": str(target)
        })

    raw_response = query_local_5090(prompt, system_prompt)
    if raw_response.startswith("Error communicating"):
        return json.dumps({"status": "error", "error": raw_response, "file": str(target)})

    code = extract_code_block(raw_response, str(target))
    syntax_ok, syntax_err = verify_syntax(code, str(target))

    with open(target, "w", encoding="utf-8") as f:
        f.write(code)

    lines = len(code.splitlines())
    tokens_saved = max(1, len(code) // 4)

    return json.dumps({
        "status": "success",
        "file": str(target),
        "lines": lines,
        "bytes": len(code.encode("utf-8")),
        "syntax_valid": syntax_ok,
        "syntax_warning": syntax_err,
        "cloud_tokens_saved": tokens_saved,
        "summary": f"Generated {lines} lines directly on RTX 5090 and written to disk ($0 cloud cost)."
    }, indent=2)


def patch_file_5090(
    file_path: str,
    instruction: str,
    search_block: Optional[str] = None
) -> str:
    """
    Direct-to-Disk Patching:
    Updates an existing file using local 5090 inference and writes directly to disk.
    """
    target = Path(file_path).resolve()
    if not target.exists():
        return json.dumps({"status": "error", "error": f"File {target} does not exist.", "file": str(target)})

    with open(target, "r", encoding="utf-8") as f:
        original = f.read()

    system_prompt = (
        "You are an expert programming assistant. Apply the requested code modification. "
        "Output ONLY the modified replacement code block or the complete updated file content. "
        "Do not include conversational commentary. Enclose code in triple backticks."
    )

    if search_block:
        prompt = (
            f"File: {target.name}\n\nExisting code block to replace:\n```\n{search_block}\n```\n\n"
            f"Instruction: {instruction}\n\n"
            "Output the updated replacement code block."
        )
    else:
        prompt = (
            f"File: {target.name}\n\nExisting file content:\n```\n{original}\n```\n\n"
            f"Instruction: {instruction}\n\n"
            "Output the complete updated file."
        )

    raw_response = query_local_5090(prompt, system_prompt)
    if raw_response.startswith("Error communicating"):
        return json.dumps({"status": "error", "error": raw_response, "file": str(target)})

    new_block = extract_code_block(raw_response, str(target))

    if search_block and search_block in original:
        updated_content = original.replace(search_block, new_block, 1)
    else:
        updated_content = new_block

    syntax_ok, syntax_err = verify_syntax(updated_content, str(target))

    with open(target, "w", encoding="utf-8") as f:
        f.write(updated_content)

    lines = len(updated_content.splitlines())
    tokens_saved = max(1, len(updated_content) // 4)

    return json.dumps({
        "status": "success",
        "file": str(target),
        "lines": lines,
        "syntax_valid": syntax_ok,
        "syntax_warning": syntax_err,
        "cloud_tokens_saved": tokens_saved,
        "summary": f"Patched {target.name} directly on RTX 5090 at $0 cloud token cost."
    }, indent=2)


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
                elif t_name == "generate_and_write_file_5090":
                    text = generate_and_write_file_5090(
                        file_path=t_args.get("file_path", ""),
                        prompt=t_args.get("prompt", ""),
                        system_prompt=t_args.get("system_prompt", "You are an expert programming assistant. Output ONLY the code enclosed in triple backticks. Do not include conversational commentary."),
                        overwrite=t_args.get("overwrite", True)
                    )
                elif t_name == "patch_file_5090":
                    text = patch_file_5090(
                        file_path=t_args.get("file_path", ""),
                        instruction=t_args.get("instruction", ""),
                        search_block=t_args.get("search_block")
                    )
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
