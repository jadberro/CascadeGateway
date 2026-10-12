#!/usr/bin/env bash
# ==============================================================================
# CascadeGateway: Local vLLM Engine Starter for NVIDIA RTX 5090 / CUDA
# ==============================================================================
set -euo pipefail

export CUDA_VISIBLE_DEVICES=0

MODEL_NAME="${VLLM_MODEL:-Qwen/Qwen2.5-Coder-32B-Instruct-AWQ}"
PORT="${VLLM_PORT:-8000}"
HOST="${VLLM_HOST:-127.0.0.1}"
MAX_LEN="${VLLM_MAX_LEN:-32768}"
GPU_UTIL="${VLLM_GPU_UTIL:-0.92}"

echo "===================================================================="
echo " Starting local vLLM server on RTX 5090 (${HOST}:${PORT})"
echo " Model: ${MODEL_NAME}"
echo " Max Context: ${MAX_LEN} | GPU Utilization: ${GPU_UTIL}"
echo " Prefix Caching: ENABLED | Chunked Prefill: ENABLED"
echo "===================================================================="

# Start vLLM OpenAI-compatible API server
exec python3 -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_NAME}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --dtype auto \
  --gpu-memory-utilization "${GPU_UTIL}" \
  --max-model-len "${MAX_LEN}" \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --trust-remote-code
