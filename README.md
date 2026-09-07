# CascadeGateway ⚡
### Hardware-Adaptive Local LLM & Cloud Cascading Architecture

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Ollama Compatible](https://img.shields.io/badge/Local_Inference-Ollama-purple.svg)](https://ollama.com)
[![OpenAI Compatible](https://img.shields.io/badge/API-OpenAI_Compatible-green.svg)](https://platform.openai.com)
[![Protocol](https://img.shields.io/badge/MCP-Protocol_2024--11--05-orange.svg)](https://modelcontextprotocol.io)

**CascadeGateway** is an intelligent, hardware-adaptive cascading proxy that routes AI queries between local GPUs (via Ollama at **$0 token cost**) and frontier cloud models (Google Gemini, OpenAI, etc.). 

It automatically detects your GPU hardware and physical VRAM on startup—whether running a flagship **RTX 5090 (32GB)**, **RTX 4090 (24GB)**, mainstream **RTX 3080 (10GB)**, or Apple Silicon—and dynamically selects and sizes the optimal models without requiring manual reconfiguration.

```mermaid
flowchart TD
    Client[AI Client: Antigravity / Cursor / Continue.dev / Script] --> Gateway[CascadeGateway :8000/v1]
    Gateway --> Profiler[Hardware Profiler & VRAM Sizer]
    Profiler --> TierCheck{Detected VRAM Tier}
    TierCheck -->|>= 24GB: 5090 / 4090| Tier1[qwen2.5-coder:32b @ 65+ t/s]
    TierCheck -->|12 - 16GB: 4070 / 4080 / 3080-12GB| Tier2[qwen2.5-coder:14b @ 50+ t/s]
    TierCheck -->|8 - 10GB: 3080-10GB / 3070| Tier3[qwen2.5-coder:7b @ 90+ t/s]
    TierCheck -->|< 8GB or CPU| Tier4[qwen2.5-coder:1.5b / 3b]
    Tier1 --> Biasing[Adaptive Biasing Engine]
    Tier2 --> Biasing
    Tier3 --> Biasing
    Tier4 --> Biasing
    Biasing -->|Routine Tasks & Coding: $0 Cost| LocalOllama[Local GPU Execution: 85-95% Offload]
    Biasing -->|Extreme Context >32k or Olympiad Proofs| CloudFrontier[Cloud Fallback: Gemini / OpenAI]
```

---

## 🚀 Key Features

* **Hardware-Adaptive VRAM Sizing**: Automatically profiles your GPU (`nvidia-smi` / Metal / CPU) and assigns the optimal model tier.
* **Autonomous $0 Offload**: Resolves 85–95% of routine coding, unit tests, refactoring, and QA directly on local VRAM.
* **Dynamic Token Biasing Engine**:
  * **Adaptive Mode**: Automatically increases local GPU bias as cloud token budgets get consumed.
  * **Manual Slider**: Fine-tune local preference from 0% (Quality First) to 100% (Maximum Local Offload).
  * **Strict Local**: 100% execution on local VRAM with automatic cloud overflow only if context exceeds physical limit.
* **Native Model Context Protocol (MCP)**: Exposes a high-performance Streamable HTTP and Stdio MCP endpoint for **Google Antigravity** and **Claude Desktop**.
* **Zero-Window Background Tray App**: Runs silently in the system tray, boots with Windows/Linux, and includes single-instance mutex protection.
* **🎮 1-Click Game Mode (Instant VRAM Purge)**: Evicts loaded models from VRAM in <1s via a dedicated button on the Web UI, Windows Tray, or `POST /api/models/unload`. Frees 20–30+ GB of VRAM immediately for AAA gaming, Blender, or video editing without terminating the server. Models reload automatically on demand when coding.
* **Live Hardware Telemetry**: In-browser control center showing real-time VRAM allocation, GPU power draw (W), temperature (°C), lifetime token savings, and an interactive prompt runner.
* **Standard OpenAI-Compatible API**: Seamless drop-in replacement (`/v1/chat/completions`) for Cursor, VSCode (Continue.dev), Aider, Claude Dev, and custom scripts.

---

## 📊 VRAM Hardware Sizing Matrix

| Hardware Tier | Supported GPUs | VRAM | Recommended Coding Model | General / Reasoning | Expected Speed |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (Flagship)** | RTX 5090, 4090, 3090, A6000 | **24 GB – 32 GB** | `qwen2.5-coder:32b` | `gemma4:26b` / `deepseek-r1:32b` | **60 – 75+ tokens/sec** |
| **Tier 2 (Enthusiast)** | RTX 4080, 4070 Ti, 4070, 3080 12GB | **12 GB – 16 GB** | `qwen2.5-coder:14b` | `mistral-small:22b-q4` | **45 – 55+ tokens/sec** |
| **Tier 3 (Mainstream)** | RTX 3080 10GB, 3070, 4060 Ti, 2080 Ti | **8 GB – 10 GB** | `qwen2.5-coder:7b` | `llama3.1:8b` | **85 – 100+ tokens/sec** |
| **Tier 4 (Entry / CPU)** | RTX 3050, 4060 laptop, Apple Silicon, CPU | **< 8 GB** | `qwen2.5-coder:1.5b` / `3b` | `llama3.2:3b` | **40 – 70 tokens/sec** |

---

## ⚡ Quick Start

### Prerequisites
1. **[Ollama](https://ollama.com/)** installed and running on your machine.
2. **Python 3.10+** installed.
3. (Optional) A free API key from [Google AI Studio](https://aistudio.google.com/app/apikey) for cloud fallback.

### Windows (One-Click)
```cmd
git clone https://github.com/jadberro/CascadeGateway.git
cd CascadeGateway
setup.bat
```
*Creates `.venv`, installs dependencies, auto-generates your desktop shortcut, and places an auto-start shortcut in your Windows Startup menu.*

### Linux / macOS
```bash
git clone https://github.com/jadberro/CascadeGateway.git
cd CascadeGateway
chmod +x setup.sh
./setup.sh
```

---

## 🛠️ Connecting Your IDEs & Tools

Once running, the control center is available at **`http://127.0.0.1:8000/`**.

### 1. Google Antigravity
Add to `~/.gemini/config/mcp_config.json`:
```json
{
  "mcpServers": {
    "rtx5090-cascade": {
      "serverUrl": "http://127.0.0.1:8000/mcp"
    }
  }
}
```
*Antigravity automatically detects the toolset (`query_local_5090`, `cascade_llm`, `get_cascade_metrics`) and dynamically dispatches code generation directly to your GPU.*

### 2. Cursor
* Navigate to **Settings > Models > OpenAI API Key**.
* Set **Base URL**: `http://127.0.0.1:8000/v1`
* Set **API Key**: `cascading-local` (any string)
* Model: `cascade-auto`

### 3. Continue.dev (`config.json`)
```json
{
  "models": [
    {
      "title": "Cascade Gateway (Local + Cloud)",
      "provider": "openai",
      "model": "cascade-auto",
      "apiBase": "http://127.0.0.1:8000/v1",
      "apiKey": "cascading-local"
    }
  ],
  "tabAutocompleteModel": {
    "title": "Local Autocomplete",
    "provider": "openai",
    "model": "cascade-auto",
    "apiBase": "http://127.0.0.1:8000/v1",
    "apiKey": "cascading-local"
  }
}
```

### 4. Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "local-cascade": {
      "serverUrl": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### 5. Python OpenAI SDK
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="cascading-local"
)

response = client.chat.completions.create(
    model="cascade-auto",
    messages=[
        {"role": "user", "content": "Write an optimized LRU cache in Python."}
    ]
)
print(response.choices[0].message.content)
```

---

## ⚙️ Configuration (`config.yaml`)

```yaml
server:
  host: "127.0.0.1"
  port: 8000

local:
  provider: "ollama"
  base_url: "http://127.0.0.1:11434"
  # Set empty to let the hardware profiler auto-select the best model for your GPU
  primary_model: ""
  max_context_tokens: 32768
  timeout_seconds: 120

cloud:
  provider: "gemini"
  default_model: "gemini-2.5-flash"
  pro_model: "gemini-2.5-pro"
  max_context_tokens: 1048576

biasing:
  mode: "adaptive"        # "adaptive", "manual", or "local_only"
  bias_factor: 0.35       # Baseline local bias (0.0 to 1.0)
  cloud_token_budget: 500000
  allow_context_overflow_to_cloud: true
```

---

## 📜 License
This project is licensed under the [MIT License](LICENSE).
