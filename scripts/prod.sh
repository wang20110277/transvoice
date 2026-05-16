#!/usr/bin/env bash
# ══════════════════════════════════════════════════
# 生产环境部署脚本
# GPU 推理 + vLLM + 全部 Docker 化
#
# 部署顺序:
#   ① postgres, redis, minio    — 基础设施
#   ② agent-asr, agent-tts      — GPU 推理 (Dockerfile)
#   ③ vllm                      — GPU LLM 推理
#   ④ mcp-server (Docker)       — 用户中心
#   ⑤ agent-flow                — 编排器
#
# 用法:
#   ./scripts/prod.sh              # 完整部署
#   ./scripts/prod.sh --build      # 重新构建镜像
#   ./scripts/prod.sh --down       # 停止全部
#   ./scripts/prod.sh --status     # 查看状态
#   ./scripts/prod.sh --logs [svc] # 查看日志
# ══════════════════════════════════════════════════

set -euo pipefail
cd "$(dirname "$0")/.."

# ── 默认配置 ──
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
BUILD_FLAG=""
VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen3.5-9B}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5:9b}"
VLLM_PORT="${VLLM_PORT:-8083}"
VLLM_GPU_IDS="${VLLM_GPU_IDS:-2}"
ASR_GPU_ID="${ASR_GPU_ID:-0}"
TTS_GPU_ID="${TTS_GPU_ID:-1}"
MCP_PORT="${MCP_PORT:-9090}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[PROD]${NC} $*"; }
warn()  { echo -e "${YELLOW}[PROD]${NC} $*"; }
error() { echo -e "${RED}[PROD]${NC} $*"; exit 1; }

# ── 解析参数 ──
ACTION="deploy"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)     BUILD_FLAG="--build"; shift ;;
    --down)      ACTION="down"; shift ;;
    --status)    ACTION="status"; shift ;;
    --logs)      ACTION="logs"; shift; [[ $# -gt 0 ]] && LOG_TARGET="$1" && shift ;;
    -h|--help)
      echo "用法: $0 [--build] [--down] [--status] [--logs [svc]]"
      exit 0 ;;
    *)           error "未知参数: $1" ;;
  esac
done

# ── 停止 (逆序) ──
down() {
  info "停止所有服务 (逆序) ..."
  docker stop callbot-flow 2>/dev/null || true
  docker rm -f callbot-flow 2>/dev/null || true
  docker stop callbot-mcp 2>/dev/null || true
  docker rm -f callbot-mcp 2>/dev/null || true
  $COMPOSE --profile vllm down --remove-orphans 2>/dev/null || true
  info "已停止"
}

# ── 状态 ──
status() {
  echo ""
  printf "%-18s %-20s %-8s %-8s %s\n" "服务" "容器名" "端口" "GPU" "状态"
  printf "%-18s %-20s %-8s %-8s %s\n" "──────────" "────────────────────" "────" "────" "──────────"
  for row in "postgres|callbot-postgres|5432|-" \
             "redis|callbot-redis|6379|-" \
             "minio|callbot-minio|9000|-" \
             "agent-asr|callbot-asr|8080|${ASR_GPU_ID}" \
             "agent-tts|callbot-tts|8081|${TTS_GPU_ID}" \
             "vllm|callbot-vllm|8083|${VLLM_GPU_IDS}" \
             "mcp-server|callbot-mcp|9090|-" \
             "agent-flow|callbot-flow|8000|-"; do
    IFS='|' read -r name cid port gpu <<< "$row"
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "未启动")
    printf "%-18s %-20s %-8s %-8s %s\n" "$name" "$cid" "$port" "$gpu" "$state"
  done
  echo ""
}

# ── 健康检查 ──
wait_http() {
  local name=$1 url=$2 max=${3:-60}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    curl -sf "$url" -o /dev/null 2>/dev/null && { info "$name 已就绪"; return 0; }
    sleep 2
  done
  error "$name 未就绪，部署中止"
}

wait_container() {
  local name=$1 cid=$2 max=${3:-60}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    s=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "")
    [[ "$s" == "healthy" ]] && { info "$name 已就绪"; return 0; }
    sleep 2
  done
  error "$name 未就绪，部署中止"
}

# ── GPU 检查 ──
check_gpu() {
  info "检查 GPU ..."
  if ! command -v nvidia-smi &>/dev/null; then
    error "nvidia-smi 未找到，生产环境需要 NVIDIA GPU"
  fi
  for gid in "$ASR_GPU_ID" "$TTS_GPU_ID" "$VLLM_GPU_IDS"; do
    if ! nvidia-smi -i "$gid" &>/dev/null; then
      error "GPU $gid 不可用"
    fi
  done
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
  info "GPU 检查通过"
}

# ── MCP (Docker build) ──
start_mcp() {
  docker ps --format '{{.Names}}' | grep -q '^callbot-mcp$' && { info "MCP 已在运行"; return 0; }

  info "构建 MCP Server 镜像 ..."
  docker build -t callbot-mcp:latest ./mcp-server/java-mcp-server

  info "启动 MCP Server ..."
  docker rm -f callbot-mcp 2>/dev/null || true
  docker run -d \
    --name callbot-mcp \
    --restart unless-stopped \
    -p "${MCP_PORT}:9090" \
    callbot-mcp:latest
  wait_http "MCP Server" "http://127.0.0.1:${MCP_PORT}/mcp" 30
}

# ── 主流程 ──
deploy() {
  info "══════════════════════════════════════"
  info "  生产环境部署"
  info "══════════════════════════════════════"

  check_gpu

  [[ -f .env ]] && source .env 2>/dev/null || true
  export ASR_GPU_ID TTS_GPU_ID VLLM_GPU_IDS VLLM_MODEL VLLM_PORT
  export CALLBOT_LLM_BASE_URL="${CALLBOT_LLM_BASE_URL:-http://host.docker.internal:${VLLM_PORT}/v1}"
  export CALLBOT_LLM_MODEL="${CALLBOT_LLM_MODEL:-${VLLM_SERVED_NAME}}"
  export CALLBOT_MCP_SERVER_URL="${CALLBOT_MCP_SERVER_URL:-http://host.docker.internal:${MCP_PORT}/mcp/}"

  # ① 基础设施
  info "━━━ ① 基础设施 ━━━"
  $COMPOSE up -d $BUILD_FLAG postgres redis minio
  wait_container "PostgreSQL" "callbot-postgres" 30
  wait_container "Redis" "callbot-redis" 15
  wait_container "MinIO" "callbot-minio" 20

  # ② 推理服务 (GPU)
  info "━━━ ② ASR (GPU ${ASR_GPU_ID}) + TTS (GPU ${TTS_GPU_ID}) ━━━"
  $COMPOSE up -d $BUILD_FLAG agent-asr agent-tts
  wait_http "ASR" "http://127.0.0.1:8080/healthz" 120
  wait_http "TTS" "http://127.0.0.1:8081/healthz" 120

  # ③ LLM (vLLM)
  info "━━━ ③ vLLM (GPU ${VLLM_GPU_IDS}, ${VLLM_MODEL}) ━━━"
  $COMPOSE --profile vllm up -d $BUILD_FLAG vllm
  info "模型加载中，可能需要数分钟 ..."
  wait_http "vLLM" "http://127.0.0.1:${VLLM_PORT}/health" 180
  export CALLBOT_LLM_BASE_URL="http://host.docker.internal:${VLLM_PORT}/v1"

  # ④ MCP
  info "━━━ ④ MCP Server ━━━"
  start_mcp

  # ⑤ 编排器
  info "━━━ ⑤ agent-flow ━━━"
  $COMPOSE up -d $BUILD_FLAG agent-flow
  wait_http "agent-flow" "http://127.0.0.1:8000/healthz" 30

  echo ""
  info "══════════════════════════════════════"
  info "  生产环境部署完成"
  info "══════════════════════════════════════"
  status
  info "端点:"
  info "  Flow  http://127.0.0.1:8000"
  info "  ASR   http://127.0.0.1:8080  (GPU ${ASR_GPU_ID})"
  info "  TTS   http://127.0.0.1:8081  (GPU ${TTS_GPU_ID})"
  info "  LLM   http://127.0.0.1:${VLLM_PORT}/v1 (GPU ${VLLM_GPU_IDS})"
  info "  MCP   http://127.0.0.1:${MCP_PORT}/mcp"
  info "  MinIO http://127.0.0.1:9001"
}

case "$ACTION" in
  deploy) deploy ;;
  down)   down ;;
  status) status ;;
  logs)
    if [[ -n "${LOG_TARGET:-}" ]]; then
      $COMPOSE --profile vllm logs -f "$LOG_TARGET"
    else
      $COMPOSE --profile vllm logs -f
    fi
    ;;
esac
