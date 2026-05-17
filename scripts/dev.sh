#!/usr/bin/env bash
# ══════════════════════════════════════════════════
# 本地开发环境启动脚本
# 基础设施 Docker + 推理/编排 conda 本地运行
#
# 部署顺序:
#   ① postgres, redis, minio    — Docker 基础设施
#   ② alembic migrate           — DB 迁移
#   ③ agent-asr, agent-tts      — conda 本地 (SenseVoice, CosyVoice3)
#   ④ ollama + mcp-server       — LLM + 用户中心
#   ⑤ agent-flow                — conda 本地 (LangGraph 编排)
#
# 前置条件:
#   conda environments: sensevoice, cosyvoice, agent-flow
#   models/: SenseVoiceSmall/, CosyVoice3-0.5B/
#   CosyVoice runtime: ~/Documents/project/CosyVoice
#   ollama (native install)
#   JDK (for MCP build)
#
# 用法:
#   ./scripts/dev.sh              # 完整启动
#   ./scripts/dev.sh --skip llm   # 跳过 LLM
#   ./scripts/dev.sh --down       # 停止全部
#   ./scripts/dev.sh --status     # 查看状态
#   ./scripts/dev.sh --logs [svc] # 查看日志
# ══════════════════════════════════════════════════

set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

# ── 默认配置 ──
COMPOSE="docker compose"
SKIP_SERVICES=()
LOG_TARGET=""

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[DEV]${NC} $*"; }
warn()  { echo -e "${YELLOW}[DEV]${NC} $*"; }
error() { echo -e "${RED}[DEV]${NC} $*" >&2; }

skip() { [[ ${#SKIP_SERVICES[@]} -gt 0 ]] && printf '%s\n' "${SKIP_SERVICES[@]}" | grep -q "^$1$" 2>/dev/null; }

# ── 解析参数 ──
ACTION="deploy"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip)      SKIP_SERVICES+=("$2"); shift 2 ;;
    --down)      ACTION="down"; shift ;;
    --status)    ACTION="status"; shift ;;
    --logs)      ACTION="logs"; shift; [[ $# -gt 0 ]] && LOG_TARGET="$1" && shift ;;
    -h|--help)
      echo "用法: $0 [--skip asr|tts|llm|mcp|flow] [--down] [--status] [--logs [svc]]"
      exit 0 ;;
    *)           error "未知参数: $1"; exit 1 ;;
  esac
done

# ── PID 文件 ──
PID_DIR="/tmp/callbot-dev-pids"
mkdir -p "$PID_DIR"

# ── 停止 ──
down() {
  info "停止所有服务 ..."

  # 停止本地进程
  for svc in asr tts flow mcp; do
    local pidfile="$PID_DIR/${svc}.pid"
    if [[ -f "$pidfile" ]]; then
      local pid
      pid=$(cat "$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        info "停止 $svc (PID $pid) ..."
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done

  # 停止 Docker 服务 (postgres, redis, minio, mcp)
  $COMPOSE down --remove-orphans 2>/dev/null || true

  info "已停止"
}

# ── 状态 ──
status() {
  echo ""
  printf "${CYAN}%-16s %-10s %-8s %s${NC}\n" "服务" "方式" "端口" "状态"
  printf "%-16s %-10s %-8s %s\n" "────────────────" "──────" "────" "──────────────"

  # Docker 服务
  for row in "PostgreSQL|docker|5432|callbot-postgres" \
             "Redis|docker|6379|callbot-redis" \
             "MinIO|docker|9000|callbot-minio"; do
    IFS='|' read -r name mode port cid <<< "$row"
    local s
    s=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "未启动")
    printf "%-16s %-10s %-8s %s\n" "$name" "$mode" "$port" "$s"
  done

  # 本地服务
  for row in "ASR|conda|8080|/healthz" \
             "TTS|conda|8081|/healthz" \
             "Ollama|native|11434|/api/tags" \
             "agent-flow|conda|8000|/healthz"; do
    IFS='|' read -r name mode port health <<< "$row"
    local s="未启动"
    if curl -sf "http://127.0.0.1:${port}${health}" -o /dev/null 2>/dev/null; then
      s="running"
    fi
    printf "%-16s %-10s %-8s %s\n" "$name" "$mode" "$port" "$s"
  done

  # MCP (Docker 容器, POST 端点)
  local mcp_s="未启动"
  if curl -sf -X POST "http://127.0.0.1:${MCP_PORT:-9090}/mcp" \
       -H "Content-Type: application/json" \
       -H "Accept: application/json, text/event-stream" \
       -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"ping","version":"0.1"}},"id":1}' \
       -o /dev/null 2>/dev/null; then
    mcp_s="running"
  fi
  printf "%-16s %-10s %-8s %s\n" "MCP" "docker" "${MCP_PORT:-9090}" "$mcp_s"
  echo ""
}

# ── 健康检查 ──
wait_http() {
  local name=$1 url=$2 max=${3:-30}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    if curl -sf "$url" -o /dev/null 2>/dev/null; then
      info "$name 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "$name 在 ${max} 次检查后未就绪，继续部署"
}

wait_container() {
  local name=$1 cid=$2 max=${3:-30}
  info "等待 $name 就绪 ..."
  for i in $(seq 1 "$max"); do
    local s
    s=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "")
    [[ "$s" == "healthy" ]] && { info "$name 已就绪"; return 0; }
    sleep 2
  done
  warn "$name 在 ${max} 次检查后未就绪，继续部署"
}

# ── 本地服务启动 (conda) ──
start_conda_svc() {
  local svc_name=$1 conda_env=$2 port=$3 pidfile=$4; shift 4
  # 剩余参数: env + cmd
  local log_file="$PID_DIR/${svc_name}.log"

  # 如果已经运行则跳过
  if curl -sf "http://127.0.0.1:${port}/healthz" -o /dev/null 2>/dev/null; then
    info "$svc_name 已在运行 (port $port)"
    return 0
  fi

  info "启动 $svc_name (conda:$conda_env, port $port) ..."
  conda run -n "$conda_env" bash -c "$*" > "$log_file" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_DIR/${pidfile}.pid"
}

# ── MCP (Docker, docker-compose) ──
start_mcp() {
  skip mcp && { info "跳过 MCP Server"; return 0; }

  # 已在运行则跳过
  if curl -sf -X POST "http://127.0.0.1:${MCP_PORT:-9090}/mcp" \
       -H "Content-Type: application/json" \
       -H "Accept: application/json, text/event-stream" \
       -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"ping","version":"0.1"}},"id":1}' \
       -o /dev/null 2>/dev/null; then
    info "MCP Server 已在运行"
    return 0
  fi

  local mcp_dir="$PROJECT_DIR/mcp-server/java-mcp-server"
  if [[ ! -d "$mcp_dir" ]]; then
    warn "MCP Server 目录不存在: $mcp_dir"
    return 0
  fi

  info "构建 MCP Server ..."
  (cd "$mcp_dir" && JAVA_HOME=/opt/homebrew/opt/openjdk ./mvnw -q package -DskipTests=true 2>/dev/null) \
    || { warn "MCP 构建失败，跳过"; return 0; }

  # 将 jar 复制为 docker-compose 挂载的固定路径
  local jar
  jar=$(find "$mcp_dir/target" -name '*.jar' ! -name '*sources*' ! -name '*javadoc*' | head -1)
  [[ -z "$jar" ]] && { warn "MCP jar 未找到，跳过"; return 0; }
  cp -f "$jar" "$mcp_dir/target/app.jar"

  info "启动 MCP Server (docker compose, port ${MCP_PORT:-9090}) ..."
  $COMPOSE up -d mcp

  # MCP 是 POST 端点，用 curl -X POST 做健康检查
  info "等待 MCP Server 就绪 ..."
  for i in $(seq 1 20); do
    if curl -sf -X POST "http://127.0.0.1:${mcp_port}/mcp" \
         -H "Content-Type: application/json" \
       -H "Accept: application/json, text/event-stream" \
         -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"ping","version":"0.1"}},"id":1}' \
         -o /dev/null 2>/dev/null; then
      info "MCP Server 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "MCP Server 在 20 次检查后未就绪，继续部署"
}

# ── 日志 ──
show_logs() {
  if [[ -n "$LOG_TARGET" ]]; then
    local log_file="$PID_DIR/${LOG_TARGET}.log"
    if [[ -f "$log_file" ]]; then
      tail -f "$log_file"
    else
      error "日志文件不存在: $log_file"
      error "可用: asr, tts, mcp, flow"
    fi
  else
    # Docker 日志
    $COMPOSE logs -f
  fi
}

# ── 主流程 ──
deploy() {
  info "══════════════════════════════════════"
  info "  本地开发环境启动"
  info "══════════════════════════════════════"

  [[ -f .env ]] && source .env 2>/dev/null || true

  # ── ① 基础设施 (Docker) ──
  info "━━━ ① 基础设施 (Docker) ━━━"
  $COMPOSE up -d postgres redis minio
  wait_container "PostgreSQL" "callbot-postgres" 30
  wait_container "Redis" "callbot-redis" 15
  wait_container "MinIO" "callbot-minio" 20

  # ── ② DB 迁移 ──
  info "━━━ ② DB 迁移 ━━━"
  # 确保 callbot schema 和 vector 扩展存在
  docker exec callbot-postgres psql -U postgres -d callbot \
    -c "CREATE SCHEMA IF NOT EXISTS callbot; CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true

  conda run -n agent-flow bash -c \
    "cd '$PROJECT_DIR/agent-flow' && PYTHONPATH=\$(pwd)/src \
     CALLBOT_PG_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/callbot' \
     alembic upgrade head" 2>&1 | tail -5
  info "DB 迁移完成"

  # ── ③ ASR + TTS (conda 本地) ──
  info "━━━ ③ ASR + TTS (conda 本地) ━━━"

  if ! skip asr; then
    start_conda_svc "ASR" "sensevoice" 8080 "asr" \
      "export PYTHONPATH='$PROJECT_DIR/agent-asr' \
       MODEL_DIR='$PROJECT_DIR/models/SenseVoiceSmall' \
       && uvicorn asradapter.main:app --host 0.0.0.0 --port 8080"
    wait_http "ASR" "http://127.0.0.1:8080/healthz" 60
  else
    info "跳过 ASR"
  fi

  if ! skip tts; then
    start_conda_svc "TTS" "cosyvoice" 8081 "tts" \
      "export PYTHONPATH='$PROJECT_DIR/agent-tts' \
       MODEL_DIR='$PROJECT_DIR/models/CosyVoice3-0.5B' \
       COSYVOICE_RUNTIME='$HOME/Documents/project/CosyVoice' \
       VOICES_DIR='$PROJECT_DIR/agent-tts/ttsadapter/voices' \
       TTS_CACHE_DIR='/tmp/tts_cache' \
       && uvicorn ttsadapter.main:app --host 0.0.0.0 --port 8081"
    wait_http "TTS" "http://127.0.0.1:8081/healthz" 120
  else
    info "跳过 TTS"
  fi

  # ── ④ Ollama + MCP ──
  info "━━━ ④ LLM + MCP ━━━"

  if ! skip llm; then
    # 确保 ollama serve 在运行
    if ! curl -sf "http://127.0.0.1:11434/api/tags" -o /dev/null 2>/dev/null; then
      info "启动 Ollama ..."
      ollama serve > "$PID_DIR/ollama.log" 2>&1 &
      echo $! > "$PID_DIR/ollama.pid"
      sleep 3
    fi

    OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"
    # 确保模型已拉取
    if ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
      info "拉取模型 $OLLAMA_MODEL ..."
      ollama pull "$OLLAMA_MODEL"
    fi
    wait_http "Ollama" "http://127.0.0.1:11434/api/tags" 15
    info "Ollama 就绪 (模型: $OLLAMA_MODEL)"
  else
    info "跳过 LLM"
  fi

  start_mcp

  # ── ⑤ agent-flow (conda 本地) ──
  info "━━━ ⑤ agent-flow (conda 本地) ━━━"

  if ! skip flow; then
    start_conda_svc "agent-flow" "agent-flow" 8000 "flow" \
      "export PYTHONPATH='$PROJECT_DIR/agent-flow:$PROJECT_DIR/agent-flow/src' \
       CALLBOT_PG_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/callbot' \
       CALLBOT_LLM_BASE_URL='http://127.0.0.1:11434/v1' \
       CALLBOT_LLM_MODEL='${OLLAMA_MODEL:-qwen3.5:9b}' \
       CALLBOT_MCP_SERVER_URL='http://127.0.0.1:9090/mcp' \
       && cd '$PROJECT_DIR/agent-flow' \
       && uvicorn main:app --host 0.0.0.0 --port 8000"
    wait_http "agent-flow" "http://127.0.0.1:8000/healthz" 30
  else
    info "跳过 agent-flow"
  fi

  echo ""
  info "══════════════════════════════════════"
  info "  本地开发环境启动完成"
  info "══════════════════════════════════════"
  status
  info "端点:"
  info "  Flow  http://127.0.0.1:8000"
  info "  ASR   http://127.0.0.1:8080"
  info "  TTS   http://127.0.0.1:8081"
  info "  LLM   http://127.0.0.1:11434/v1"
  info "  MCP   http://127.0.0.1:9090/mcp"
  info "  MinIO http://127.0.0.1:9001 (admin/changeme123)"
  info "日志: ./scripts/dev.sh --logs [asr|tts|mcp|flow]"
}

case "$ACTION" in
  deploy) deploy ;;
  down)   down ;;
  status) status ;;
  logs)   show_logs ;;
esac
