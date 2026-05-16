#!/usr/bin/env bash
# 生产环境：使用 vLLM 启动 LLM 推理
# 用法: ./scripts/vllm.sh [start|stop|logs]
# GPU: 2 (可通过 VLLM_GPU_IDS 覆盖)

set -euo pipefail

MODEL="${VLLM_MODEL:-Qwen/Qwen3.5-9B}"
SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-9b}"
PORT="${VLLM_PORT:-8083}"
GPU_IDS="${VLLM_GPU_IDS:-2}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
GPU_MEM="${VLLM_GPU_MEMORY_UTILIZATION:-0.9}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"

case "${1:-start}" in
  start)
    echo ">>> 启动 vLLM (模型=${MODEL}, GPU=${GPU_IDS}, 端口=${PORT}) ..."
    docker run -d \
      --name callbot-llm \
      --gpus "\"device=${GPU_IDS}\"" \
      -v "${HF_CACHE}:/root/.cache/huggingface" \
      -p "${PORT}:8083" \
      --restart unless-stopped \
      vllm/vllm-openai:latest \
        --model "${MODEL}" \
        --served-model-name "${SERVED_NAME}" \
        --host 0.0.0.0 \
        --port 8083 \
        --max-model-len "${MAX_MODEL_LEN}" \
        --gpu-memory-utilization "${GPU_MEM}" \
        --trust-remote-code
    echo ">>> 等待模型加载 ..."
    for i in $(seq 1 60); do
      if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        echo ">>> vLLM 已就绪"
        echo "    API:    http://127.0.0.1:${PORT}/v1"
        echo "    模型名: ${SERVED_NAME}"
        echo ""
        echo "    docker compose 环境变量:"
        echo "    CALLBOT_LLM_BASE_URL=http://host.docker.internal:${PORT}/v1"
        echo "    CALLBOT_LLM_MODEL=${SERVED_NAME}"
        exit 0
      fi
      sleep 2
    done
    echo "错误: vLLM 启动超时，请检查日志: $0 logs"
    exit 1
    ;;

  stop)
    echo ">>> 停止 vLLM ..."
    docker stop callbot-llm 2>/dev/null || true
    docker rm callbot-llm 2>/dev/null || true
    echo ">>> 已停止"
    ;;

  logs)
    docker logs -f callbot-llm 2>/dev/null || echo "容器未运行"
    ;;

  *)
    echo "用法: $0 {start|stop|logs}"
    exit 1
    ;;
esac
