#!/usr/bin/env bash
# 本地开发：使用 Ollama 启动 LLM 推理
# 用法: ./scripts/ollama.sh [pull|start|stop]

set -euo pipefail

MODEL="${OLLAMA_MODEL:-qwen3:8b}"
PORT="${OLLAMA_PORT:-8083}"

case "${1:-start}" in
  pull)
    echo ">>> 拉取模型 ${MODEL} ..."
    ollama pull "${MODEL}"
    echo ">>> 完成"
    ;;

  start)
    echo ">>> 启动 Ollama 服务 (模型=${MODEL}, 端口=${PORT}) ..."
    if ! command -v ollama &>/dev/null; then
      echo "错误: 未安装 ollama，请先运行: curl -fsSL https://ollama.com/install.sh | sh"
      exit 1
    fi

    # 确保 ollama 服务运行
    ollama list &>/dev/null || ollama serve &>/dev/null &
    sleep 2

    # 检查模型是否已拉取
    if ! ollama list | grep -q "${MODEL%%:*}"; then
      echo ">>> 模型未找到，自动拉取 ${MODEL} ..."
      ollama pull "${MODEL}"
    fi

    # 设置 Ollama 监听端口
    export OLLAMA_HOST="0.0.0.0:${PORT}"
    echo ">>> Ollama 已就绪"
    echo "    API:    http://127.0.0.1:${PORT}/v1"
    echo "    模型名: ${MODEL}"
    echo ""
    echo "    docker compose 环境变量:"
    echo "    CALLBOT_LLM_BASE_URL=http://host.docker.internal:${PORT}/v1"
    echo "    CALLBOT_LLM_MODEL=${MODEL}"
    ;;

  stop)
    echo ">>> 停止 Ollama ..."
    pkill -f "ollama serve" 2>/dev/null || true
    echo ">>> 已停止"
    ;;

  *)
    echo "用法: $0 {pull|start|stop}"
    exit 1
    ;;
esac
