#!/usr/bin/env bash
# ══════════════════════════════════════════════════
# 本地启动脚本 — ASR / TTS / Flow 逐个启动
#
# 用法:
#   ./scripts/local.sh              # 启动全部
#   ./scripts/local.sh asr          # 仅启动 ASR
#   ./scripts/local.sh tts          # 仅启动 TTS
#   ./scripts/local.sh flow         # 仅启动 Flow
#   ./scripts/local.sh asr tts      # 启动 ASR + TTS
#   ./scripts/local.sh stop         # 停止全部
#   ./scripts/local.sh status       # 查看状态
# ══════════════════════════════════════════════════

set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[LOCAL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[LOCAL]${NC} $*"; }
error() { echo -e "${RED}[LOCAL]${NC} $*" >&2; }

PID_DIR="/tmp/callbot-local-pids"
LOG_DIR="/tmp/callbot-local-logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# ── 配置 ──
ASR_PORT=8080
TTS_PORT=8081
FLOW_PORT=8000

ASR_MODEL_DIR="$PROJECT_DIR/models/SenseVoiceSmall"
TTS_MODEL_DIR="$PROJECT_DIR/models/CosyVoice3-0.5B"
TTS_VOICES_DIR="$PROJECT_DIR/voices"
TTS_CACHE_DIR="/tmp/tts_cache"
COSYVOICE_RUNTIME="$HOME/Documents/project/CosyVoice"

# ── 工具函数 ──
is_running() {
  local port=$1
  curl -sf "http://127.0.0.1:${port}/healthz" -o /dev/null 2>/dev/null
}

wait_http() {
  local name=$1 port=$2 max=${3:-60}
  info "等待 $name 就绪 (port $port) ..."
  for i in $(seq 1 "$max"); do
    if is_running "$port"; then
      info "$name 已就绪"
      return 0
    fi
    sleep 2
  done
  warn "$name 在 $((max * 2))s 后未就绪"
  return 1
}

get_pid() {
  local svc=$1 pidfile="$PID_DIR/${svc}.pid"
  [[ -f "$pidfile" ]] && cat "$pidfile" || echo ""
}

stop_svc() {
  local svc=$1
  local pid
  pid=$(get_pid "$svc")
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    info "停止 $svc (PID $pid) ..."
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_DIR/${svc}.pid"
}

# ── 启动函数 ──
start_asr() {
  if is_running "$ASR_PORT"; then
    info "ASR 已在运行 (port $ASR_PORT)"
    return 0
  fi

  info "启动 ASR (SenseVoice, port $ASR_PORT) ..."
  conda run -n sensevoice bash -c \
    "cd '$PROJECT_DIR/agent-asr/asradapter' \
     && PYTHONPATH='$PROJECT_DIR/agent-asr' \
        MODEL_DIR='$ASR_MODEL_DIR' \
        uvicorn main:app --host 0.0.0.0 --port $ASR_PORT \
        >> '$LOG_DIR/asr.log' 2>&1" &
  echo $! > "$PID_DIR/asr.pid"
  wait_http "ASR" "$ASR_PORT" 60
}

start_tts() {
  if is_running "$TTS_PORT"; then
    info "TTS 已在运行 (port $TTS_PORT)"
    return 0
  fi

  info "启动 TTS (CosyVoice, port $TTS_PORT) ..."
  conda run -n cosyvoice bash -c \
    "cd '$PROJECT_DIR/agent-tts/ttsadapter' \
     && PYTHONPATH='$PROJECT_DIR/agent-tts' \
        MODEL_DIR='$TTS_MODEL_DIR' \
        VOICES_DIR='$TTS_VOICES_DIR' \
        TTS_CACHE_DIR='$TTS_CACHE_DIR' \
        COSYVOICE_RUNTIME='$COSYVOICE_RUNTIME' \
        uvicorn main:app --host 0.0.0.0 --port $TTS_PORT \
        >> '$LOG_DIR/tts.log' 2>&1" &
  echo $! > "$PID_DIR/tts.pid"
  wait_http "TTS" "$TTS_PORT" 120
}

start_flow() {
  if is_running "$FLOW_PORT"; then
    info "agent-flow 已在运行 (port $FLOW_PORT)"
    return 0
  fi

  info "启动 agent-flow (port $FLOW_PORT) ..."
  conda run -n agent-flow bash -c \
    "cd '$PROJECT_DIR/agent-flow' \
     && PYTHONPATH='$PROJECT_DIR/agent-flow:$PROJECT_DIR/agent-flow/src' \
        uvicorn main:app --host 0.0.0.0 --port $FLOW_PORT \
        >> '$LOG_DIR/flow.log' 2>&1" &
  echo $! > "$PID_DIR/flow.pid"
  wait_http "agent-flow" "$FLOW_PORT" 30
}

show_status() {
  echo ""
  printf "${CYAN}%-16s %-10s %-8s %-8s %s${NC}\n" "服务" "引擎" "端口" "状态" "PID"
  printf "%-16s %-10s %-8s %-8s %s\n" "────────────────" "──────" "────" "──────" "─────"

  for row in "ASR|SenseVoice|$ASR_PORT|asr" \
             "TTS|CosyVoice|$TTS_PORT|tts" \
             "agent-flow|LangGraph|$FLOW_PORT|flow"; do
    IFS='|' read -r name engine port svc <<< "$row"
    local s="stopped" pid
    pid=$(get_pid "$svc")
    if is_running "$port"; then
      s="running"
    fi
    printf "%-16s %-10s %-8s %-8s %s\n" "$name" "$engine" "$port" "$s" "${pid:--}"
  done
  echo ""
}

stop_all() {
  info "停止所有服务 ..."
  stop_svc asr
  stop_svc tts
  stop_svc flow
  info "已停止"
}

# ── 参数解析 ──
SERVICES=()
ACTION="start"

for arg in "$@"; do
  case "$arg" in
    stop)    ACTION="stop" ;;
    status)  ACTION="status" ;;
    -h|--help)
      echo "用法: $0 [asr|tts|flow|stop|status]"
      echo "  无参数   启动全部"
      echo "  asr      仅启动 ASR"
      echo "  tts      仅启动 TTS"
      echo "  flow     仅启动 agent-flow"
      echo "  stop     停止全部"
      echo "  status   查看状态"
      exit 0 ;;
    *)       SERVICES+=("$arg") ;;
  esac
done

# ── 执行 ──
case "$ACTION" in
  stop)   stop_all ;;
  status) show_status ;;
  start)
    info "══════════════════════════════════════"
    info "  本地服务启动"
    info "══════════════════════════════════════"

    # 默认全部
    [[ ${#SERVICES[@]} -eq 0 ]] && SERVICES=(asr tts flow)

    for svc in "${SERVICES[@]}"; do
      case "$svc" in
        asr)  start_asr ;;
        tts)  start_tts ;;
        flow) start_flow ;;
        *)    error "未知服务: $svc (可选: asr, tts, flow)" ;;
      esac
    done

    echo ""
    show_status
    info "日志目录: $LOG_DIR/"
    info "  tail -f $LOG_DIR/asr.log"
    info "  tail -f $LOG_DIR/tts.log"
    info "  tail -f $LOG_DIR/flow.log"
    ;;
esac
