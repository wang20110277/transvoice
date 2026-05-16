#!/usr/bin/env bash
# 智能外呼系统 — 全栈部署脚本
#
# 部署顺序（严格按依赖链）：
#   ① postgres, redis, minio   — 基础设施，无依赖
#   ② agent-asr, agent-tts     — 推理服务，依赖 MinIO
#   ③ LLM (ollama/vllm)        — LLM 推理，ASR/TTS 之后
#   ④ mcp-server               — Java MCP，LLM 之后
#   ⑤ agent-flow               — 编排器，依赖以上所有
#
# 用法:
#   ./scripts/deploy.sh              # 完整部署 (ollama)
#   ./scripts/deploy.sh --llm vllm   # 完整部署 (vllm)
#   ./scripts/deploy.sh --build      # 重新构建镜像后部署
#   ./scripts/deploy.sh --down       # 停止全部服务
#   ./scripts/deploy.sh --status     # 查看服务状态
#   ./scripts/deploy.sh --logs       # 查看全部日志
#   ./scripts/deploy.sh --logs agent-flow  # 查看指定服务日志

set -euo pipefail
cd "$(dirname "$0")/.."

# ── 配置 ──
COMPOSE="docker compose"
LLM_PROFILE="${LLM_PROFILE:-ollama}"
BUILD_FLAG=""
MCP_DIR="./mcp-server/java-mcp-server"
MCP_PORT=9090

# ── 颜色 ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 解析参数 ──
ACTION="deploy"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm)       LLM_PROFILE="$2"; shift 2 ;;
    --build)     BUILD_FLAG="--build"; shift ;;
    --down)      ACTION="down"; shift ;;
    --status)    ACTION="status"; shift ;;
    --logs)      ACTION="logs"; shift; [[ $# -gt 0 ]] && LOG_TARGET="$1" && shift ;;
    -h|--help)
      echo "用法: $0 [--llm ollama|vllm] [--build] [--down] [--status] [--logs [service]]"
      exit 0 ;;
    *)           error "未知参数: $1" ;;
  esac
done

# ── 停止 ──
down() {
  info "停止所有服务 ..."
  # 停止编排器
  docker stop callbot-flow 2>/dev/null && docker rm callbot-flow 2>/dev/null || true
  # 停止 MCP
  docker stop callbot-mcp 2>/dev/null && docker rm callbot-mcp 2>/dev/null || true
  # 停止 LLM
  docker stop callbot-ollama callbot-vllm 2>/dev/null && docker rm callbot-ollama callbot-vllm 2>/dev/null || true
  # 停止 compose 服务
  $COMPOSE --profile "$LLM_PROFILE" down --remove-orphans 2>/dev/null || true
  info "所有服务已停止"
}

# ── 查看状态 ──
status() {
  echo ""
  printf "%-20s %-12s %-10s %s\n" "服务" "容器名" "端口" "状态"
  printf "%-20s %-12s %-10s %s\n" "────" "──────" "────" "────"
  for svc in "postgres:callbot-postgres:5432" \
             "redis:callbot-redis:6379" \
             "minio:callbot-minio:9000" \
             "agent-asr:callbot-asr:8080" \
             "agent-tts:callbot-tts:8081" \
             "agent-flow:callbot-flow:8000"; do
    name="${svc%%:*}"; cid="${svc#*:}"; cid="${cid%%:*}"; port="${svc##*:}"
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "未启动")
    printf "%-20s %-12s %-10s %s\n" "$name" "$cid" "$port" "$state"
  done
  # LLM
  for cid in callbot-ollama callbot-vllm; do
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "未启动")
    [[ "$state" != "未启动" ]] && printf "%-20s %-12s %-10s %s\n" "llm" "$cid" "8083" "$state"
  done
  # MCP
  state=$(docker inspect -f '{{.State.Status}}' callbot-mcp 2>/dev/null || echo "未启动")
  printf "%-20s %-12s %-10s %s\n" "mcp-server" "callbot-mcp" "9090" "$state"
  echo ""
}

# ── 健康检查 ──
wait_healthy() {
  local name=$1 url=$2 max=${3:-30}
  info "等待 $name 就绪 ($url) ..."
  for i in $(seq 1 "$max"); do
    if curl -sf "$url" -o /dev/null 2>/dev/null; then
      info "$name 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "$name 在 ${max} 次尝试后未就绪，继续部署"
  return 0
}

wait_container_healthy() {
  local name=$1 cid=$2 max=${3:-30}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    state=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")
    if [[ "$state" == "healthy" ]]; then
      info "$name 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "$name 在 ${max} 次尝试后未就绪，继续部署"
  return 0
}

# ── MCP Server (Java, 独立容器) ──
start_mcp() {
  if docker ps --format '{{.Names}}' | grep -q '^callbot-mcp$'; then
    info "MCP Server 已在运行"
    return 0
  fi

  info "构建 MCP Server ..."
  cd "$MCP_DIR"
  JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw -q package -DskipDependencies=false -DskipTests=true 2>/dev/null \
    || JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw -q package -DskipTests=true
  cd -

  info "启动 MCP Server ..."
  docker rm -f callbot-mcp 2>/dev/null || true
  docker run -d \
    --name callbot-mcp \
    --restart unless-stopped \
    -p "${MCP_PORT}:9090" \
    -e SPRING_AI_MCP_SERVER_STATELESS_MCP_ENDPOINT=/mcp \
    mcp-server/java-mcp-server:latest 2>/dev/null || {
      # 如果没有打镜像 tag，直接用 jar 运行
      local jar
      jar=$(find "$MCP_DIR/target" -name '*.jar' ! -name '*sources*' ! -name '*javadoc*' | head -1)
      [[ -z "$jar" ]] && { warn "MCP jar 未找到，跳过"; return 0; }
      docker run -d \
        --name callbot-mcp \
        --restart unless-stopped \
        -p "${MCP_PORT}:9090" \
        -v "$(realpath "$jar"):/app/app.jar" \
        eclipse-temurin:25-jre \
        java -jar /app/app.jar
    }
  wait_healthy "MCP Server" "http://127.0.0.1:${MCP_PORT}/mcp" 15
}

# ── 主部署流程 ──
deploy() {
  info "========================================="
  info "  智能外呼系统部署 (LLM=${LLM_PROFILE})"
  info "========================================="

  # 加载 .env
  [[ -f .env ]] && source .env 2>/dev/null || true
  # 默认 LLM 环境变量
  export CALLBOT_LLM_BASE_URL="${CALLBOT_LLM_BASE_URL:-http://host.docker.internal:8083/v1}"
  export CALLBOT_LLM_MODEL="${CALLBOT_LLM_MODEL:-qwen3:8b}"

  # ── 第①层: 基础设施 ──
  info "━━━ 第①层: 基础设施 (PostgreSQL, Redis, MinIO) ━━━"
  $COMPOSE up -d $BUILD_FLAG postgres redis minio

  wait_container_healthy "PostgreSQL" "callbot-postgres" 30
  wait_container_healthy "Redis" "callbot-redis" 15
  wait_container_healthy "MinIO" "callbot-minio" 20

  # ── 第②层: 推理服务 ──
  info "━━━ 第②层: 推理服务 (ASR, TTS) ━━━"
  $COMPOSE up -d $BUILD_FLAG agent-asr agent-tts

  wait_healthy "ASR" "http://127.0.0.1:8080/healthz" 60
  wait_healthy "TTS" "http://127.0.0.1:8081/healthz" 60

  # ── 第③层: LLM ──
  info "━━━ 第③层: LLM (${LLM_PROFILE}) ━━━"
  if [[ "$LLM_PROFILE" == "ollama" ]]; then
    $COMPOSE --profile ollama up -d $BUILD_FLAG ollama
    wait_container_healthy "Ollama" "callbot-ollama" 30 || true
    # 拉取模型（如尚未拉取）
    local model="${CALLBOT_LLM_MODEL:-qwen3:8b}"
    docker exec callbot-ollama ollama list 2>/dev/null | grep -q "${model%%:*}" || {
      info "拉取 Ollama 模型 ${model} ..."
      docker exec callbot-ollama ollama pull "$model"
    }
    wait_healthy "Ollama API" "http://127.0.0.1:8083/api/tags" 30
    export CALLBOT_LLM_BASE_URL="http://host.docker.internal:8083/v1"
  else
    $COMPOSE --profile vllm up -d $BUILD_FLAG vllm
    info "等待 vLLM 模型加载 (可能需要几分钟) ..."
    wait_healthy "vLLM" "http://127.0.0.1:8083/health" 120
    export CALLBOT_LLM_BASE_URL="http://host.docker.internal:8083/v1"
    export CALLBOT_LLM_MODEL="${VLLM_SERVED_NAME:-qwen3.5-9b}"
  fi

  # ── 第④层: MCP Server ──
  info "━━━ 第④层: MCP Server (Java) ━━━"
  start_mcp

  # ── 第⑤层: 编排器 ──
  info "━━━ 第⑤层: Orchestrator (agent-flow) ━━━"
  $COMPOSE up -d $BUILD_FLAG agent-flow

  wait_healthy "agent-flow" "http://127.0.0.1:8000/healthz" 30

  # ── 完成 ──
  echo ""
  info "========================================="
  info "  部署完成！服务状态："
  info "========================================="
  status
  info "端点:"
  info "  agent-flow : http://127.0.0.1:8000"
  info "  ASR        : http://127.0.0.1:8080"
  info "  TTS        : http://127.0.0.1:8081"
  info "  LLM        : http://127.0.0.1:8083/v1"
  info "  MCP        : http://127.0.0.1:9090/mcp"
  info "  MinIO      : http://127.0.0.1:9001 (admin/changeme123)"
}

# ── 入口 ──
case "$ACTION" in
  deploy) deploy ;;
  down)   down ;;
  status) status ;;
  logs)
    if [[ -n "${LOG_TARGET:-}" ]]; then
      $COMPOSE logs -f "$LOG_TARGET"
    else
      $COMPOSE logs -f
    fi
    ;;
esac
