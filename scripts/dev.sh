#!/usr/bin/env bash
# ══════════════════════════════════════════════════
# 开发环境部署脚本
# CPU 推理 + Ollama + 本地 MCP jar
#
# 部署顺序:
#   ① postgres, redis, minio    — 基础设施
#   ② agent-asr, agent-tts      — CPU 推理 (Dockerfile.cpu)
#   ③ ollama + 模型拉取         — LLM
#   ④ mcp-server (jar 直跑)     — 用户中心
#   ⑤ agent-flow                — 编排器
#
# 用法:
#   ./scripts/dev.sh              # 完整部署
#   ./scripts/dev.sh --build      # 重新构建镜像
#   ./scripts/dev.sh --skip llm   # 跳过 LLM (使用外部服务)
#   ./scripts/dev.sh --skip mcp   # 跳过 MCP
#   ./scripts/dev.sh --down       # 停止全部
#   ./scripts/dev.sh --status     # 查看状态
#   ./scripts/dev.sh --logs [svc] # 查看日志
# ══════════════════════════════════════════════════

set -euo pipefail
cd "$(dirname "$0")/.."

# ── 默认配置 ──
COMPOSE="docker compose"
BUILD_FLAG=""
SKIP_SERVICES=()
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
OLLAMA_PORT="${OLLAMA_PORT:-8083}"
MCP_DIR="./mcp-server/java-mcp-server"
MCP_PORT="${MCP_PORT:-9090}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[DEV]${NC} $*"; }
warn()  { echo -e "${YELLOW}[DEV]${NC} $*"; }
error() { echo -e "${RED}[DEV]${NC} $*"; exit 1; }

skip() { printf '%s\n' "${SKIP_SERVICES[@]}" | grep -q "^$1$"; }

# ── 解析参数 ──
ACTION="deploy"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)     BUILD_FLAG="--build"; shift ;;
    --skip)      SKIP_SERVICES+=("$2"); shift 2 ;;
    --down)      ACTION="down"; shift ;;
    --status)    ACTION="status"; shift ;;
    --logs)      ACTION="logs"; shift; [[ $# -gt 0 ]] && LOG_TARGET="$1" && shift ;;
    -h|--help)
      echo "用法: $0 [--build] [--skip llm|mcp|asr|tts] [--down] [--status] [--logs [svc]]"
      exit 0 ;;
    *)           error "未知参数: $1" ;;
  esac
done

# ── 停止 ──
down() {
  info "停止所有服务 ..."
  docker stop callbot-flow callbot-mcp callbot-ollama 2>/dev/null || true
  docker rm -f callbot-flow callbot-mcp callbot-ollama 2>/dev/null || true
  $COMPOSE --profile ollama down --remove-orphans 2>/dev/null || true
  info "已停止"
}

# ── 状态 ──
status() {
  echo ""
  printf "%-18s %-20s %-8s %s\n" "服务" "容器名" "端口" "状态"
  printf "%-18s %-20s %-8s %s\n" "──────────" "────────────────────" "────" "──────────"
  for row in "postgres|callbot-postgres|5432" \
             "redis|callbot-redis|6379" \
             "minio|callbot-minio|9000" \
             "agent-asr|callbot-asr|8080" \
             "agent-tts|callbot-tts|8081" \
             "ollama|callbot-ollama|8083" \
             "mcp-server|callbot-mcp|9090" \
             "agent-flow|callbot-flow|8000"; do
    IFS='|' read -r name cid port <<< "$row"
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "未启动")
    printf "%-18s %-20s %-8s %s\n" "$name" "$cid" "$port" "$state"
  done
  echo ""
}

# ── 健康检查 ──
wait_http() {
  local name=$1 url=$2 max=${3:-30}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    curl -sf "$url" -o /dev/null 2>/dev/null && { info "$name 已就绪"; return 0; }
    sleep 2
  done
  warn "$name 未就绪，继续部署"
}

wait_container() {
  local name=$1 cid=$2 max=${3:-30}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    s=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "")
    [[ "$s" == "healthy" ]] && { info "$name 已就绪"; return 0; }
    sleep 2
  done
  warn "$name 未就绪，继续部署"
}

# ── MCP (本地 jar 运行) ──
start_mcp() {
  skip mcp && { info "跳过 MCP Server"; return 0; }
  docker ps --format '{{.Names}}' | grep -q '^callbot-mcp$' && { info "MCP 已在运行"; return 0; }

  info "构建 MCP Server ..."
  cd "$MCP_DIR"
  JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw -q package -DskipTests=true 2>/dev/null \
    || { cd -; warn "MCP 构建失败，跳过"; return 0; }
  cd -

  local jar
  jar=$(find "$MCP_DIR/target" -name '*.jar' ! -name '*sources*' ! -name '*javadoc*' | head -1)
  [[ -z "$jar" ]] && { warn "MCP jar 未找到，跳过"; return 0; }

  info "启动 MCP Server ..."
  docker rm -f callbot-mcp 2>/dev/null || true
  docker run -d \
    --name callbot-mcp \
    --restart unless-stopped \
    -p "${MCP_PORT}:9090" \
    -v "$(realpath "$jar"):/app/app.jar" \
    eclipse-temurin:25-jre \
    java -jar /app/app.jar
  wait_http "MCP Server" "http://127.0.0.1:${MCP_PORT}/mcp" 15
}

# ── 主流程 ──
deploy() {
  info "══════════════════════════════════════"
  info "  开发环境部署"
  info "══════════════════════════════════════"

  [[ -f .env ]] && source .env 2>/dev/null || true
  export CALLBOT_LLM_BASE_URL="${CALLBOT_LLM_BASE_URL:-http://host.docker.internal:${OLLAMA_PORT}/v1}"
  export CALLBOT_LLM_MODEL="${CALLBOT_LLM_MODEL:-${OLLAMA_MODEL}}"

  # ① 基础设施
  info "━━━ ① 基础设施 ━━━"
  $COMPOSE up -d $BUILD_FLAG postgres redis minio
  wait_container "PostgreSQL" "callbot-postgres" 30
  wait_container "Redis" "callbot-redis" 15
  wait_container "MinIO" "callbot-minio" 20

  # ② 推理服务 (CPU)
  info "━━━ ② ASR + TTS (CPU) ━━━"
  if ! skip asr && ! skip tts; then
    $COMPOSE up -d $BUILD_FLAG agent-asr agent-tts
    skip asr || wait_http "ASR" "http://127.0.0.1:8080/healthz" 60
    skip tts || wait_http "TTS" "http://127.0.0.1:8081/healthz" 60
  else
    skip asr && info "跳过 ASR"
    skip tts && info "跳过 TTS"
  fi

  # ③ LLM (Ollama)
  if skip llm; then
    info "━━━ ③ LLM — 跳过 (使用外部 ${CALLBOT_LLM_BASE_URL}) ━━━"
  else
    info "━━━ ③ LLM (Ollama, ${OLLAMA_MODEL}) ━━━"
    $COMPOSE --profile ollama up -d $BUILD_FLAG ollama
    wait_container "Ollama" "callbot-ollama" 30 || true
    docker exec callbot-ollama ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}" || {
      info "拉取模型 ${OLLAMA_MODEL} ..."
      docker exec callbot-ollama ollama pull "$OLLAMA_MODEL"
    }
    wait_http "Ollama API" "http://127.0.0.1:${OLLAMA_PORT}/api/tags" 30
    export CALLBOT_LLM_BASE_URL="http://host.docker.internal:${OLLAMA_PORT}/v1"
  fi

  # ④ MCP
  info "━━━ ④ MCP Server ━━━"
  start_mcp

  # ⑤ 编排器
  info "━━━ ⑤ agent-flow ━━━"
  $COMPOSE up -d $BUILD_FLAG agent-flow
  wait_http "agent-flow" "http://127.0.0.1:8000/healthz" 30

  echo ""
  info "══════════════════════════════════════"
  info "  开发环境部署完成"
  info "══════════════════════════════════════"
  status
  info "端点:"
  info "  Flow  http://127.0.0.1:8000"
  info "  ASR   http://127.0.0.1:8080"
  info "  TTS   http://127.0.0.1:8081"
  info "  LLM   http://127.0.0.1:${OLLAMA_PORT}/v1"
  info "  MCP   http://127.0.0.1:${MCP_PORT}/mcp"
  info "  MinIO http://127.0.0.1:9001 (admin/changeme123)"
}

case "$ACTION" in
  deploy) deploy ;;
  down)   down ;;
  status) status ;;
  logs)
    if [[ -n "${LOG_TARGET:-}" ]]; then
      $COMPOSE --profile ollama logs -f "$LOG_TARGET"
    else
      $COMPOSE --profile ollama logs -f
    fi
    ;;
esac
