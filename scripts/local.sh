#!/usr/bin/env bash
# ══════════════════════════════════════════════════
# 本地启动脚本 — FreeSWITCH / ASR / TTS / Flow 逐个启动
#
# 用法:
#   ./scripts/local.sh              # 启动全部 (fs asr tts mcp flow console)
#   ./scripts/local.sh fs           # 仅启动 FreeSWITCH
#   ./scripts/local.sh asr          # 仅启动 ASR
#   ./scripts/local.sh tts          # 仅启动 TTS
#   ./scripts/local.sh mcp          # 仅启动 MCP Server
#   ./scripts/local.sh flow         # 仅启动 Flow
#   ./scripts/local.sh console      # 仅启动 Console (Next.js, pm2 守护)
#   ./scripts/local.sh fs asr tts   # 启动 FreeSWITCH + ASR + TTS
#   ./scripts/local.sh stop         # 停止全部
#   ./scripts/local.sh stop asr     # 仅停止 ASR
#   ./scripts/local.sh stop flow    # 仅停止 Flow
#   ./scripts/local.sh stop fs asr  # 停止 FreeSWITCH + ASR
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
FS_BIN="$HOME/freeswitch/bin/freeswitch"
FS_ESL_PORT=8021
ASR_PORT=8080
TTS_PORT=8081
MCP_PORT=9090
FLOW_PORT=8000
CONSOLE_PORT=3001

CONSOLE_DIR="$PROJECT_DIR/console/server"
ASR_MODEL_DIR="$PROJECT_DIR/agent-asr/models/SenseVoiceSmall"
TTS_MODEL_DIR="$PROJECT_DIR/agent-tts/models/CosyVoice3-0.5B"
VOICES_DIR="$PROJECT_DIR/voices"
TTS_CACHE_DIR="/tmp/tts_cache"
COSYVOICE_RUNTIME="$PROJECT_DIR/agent-tts/CosyVoice"

# ── 工具函数 ──
is_running() {
  local port=$1
  curl -sf "http://127.0.0.1:${port}/healthz" -o /dev/null 2>/dev/null
}

is_fs_running() {
  pgrep -f "freeswitch -nc" >/dev/null 2>/dev/null
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

# console (Next.js dev) 无 /healthz,用 TCP 监听判断就绪
port_listening() {
  local port=$1
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

wait_listen() {
  local name=$1 port=$2 max=${3:-60}
  info "等待 $name 就绪 (port $port) ..."
  for i in $(seq 1 "$max"); do
    if port_listening "$port"; then
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

# 按端口杀监听进程（只杀 LISTEN 状态，避免误杀作为客户端连接到该端口的进程如 FreeSWITCH）
kill_by_port() {
  local port=$1
  local pids
  pids=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null) || true
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill 2>/dev/null || true
    # 等待进程退出
    for i in $(seq 1 10); do
      pids=$(lsof -i :"$port" -sTCP:LISTEN -t 2>/dev/null) || true
      [[ -z "$pids" ]] && return 0
      sleep 0.5
    done
    # 强杀
    echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
}

# ── 停止单个服务 ──

stop_svc() {
  local svc=$1

  case "$svc" in
    fs)
      if is_fs_running; then
        info "停止 FreeSWITCH ..."
        # 先尝试 fs_cli shutdown（优雅关闭）
        "$HOME/freeswitch/bin/fs_cli" -H 127.0.0.1 -P 8021 -p ClueCon -x "shutdown" 2>/dev/null || true
        sleep 1
        # 如果还在运行，SIGTERM
        if is_fs_running; then
          pkill -f "freeswitch -nc" 2>/dev/null || true
          sleep 2
        fi
        # 如果还在运行，SIGKILL
        if is_fs_running; then
          pkill -9 -f "freeswitch -nc" 2>/dev/null || true
        fi
      else
        info "FreeSWITCH 未在运行"
      fi
      ;;
    asr)
      if is_running "$ASR_PORT"; then
        info "停止 ASR ..."
        kill_by_port "$ASR_PORT"
      fi
      local stale_asr
      stale_asr=$(pgrep -f "uvicorn main:app.*--port $ASR_PORT" 2>/dev/null) || true
      if [[ -n "$stale_asr" ]]; then
        info "清理残留 ASR 进程: $stale_asr"
        echo "$stale_asr" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$stale_asr" | xargs kill -9 2>/dev/null || true
      fi
      info "ASR 已停止"
      rm -f "$PID_DIR/asr.pid"
      ;;
    tts)
      if is_running "$TTS_PORT"; then
        info "停止 TTS ..."
        kill_by_port "$TTS_PORT"
      fi
      local stale_tts
      stale_tts=$(pgrep -f "uvicorn main:app.*--port $TTS_PORT" 2>/dev/null) || true
      if [[ -n "$stale_tts" ]]; then
        info "清理残留 TTS 进程: $stale_tts"
        echo "$stale_tts" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$stale_tts" | xargs kill -9 2>/dev/null || true
      fi
      info "TTS 已停止"
      rm -f "$PID_DIR/tts.pid"
      ;;
    flow)
      if is_running "$FLOW_PORT"; then
        info "停止 agent-flow ..."
        kill_by_port "$FLOW_PORT"
      fi
      # 杀所有残留的 uvicorn 进程（conda run 子进程可能脱离端口监听但 ESL 连接仍在）
      local stale
      stale=$(pgrep -f "uvicorn main:app.*--port $FLOW_PORT" 2>/dev/null) || true
      if [[ -n "$stale" ]]; then
        info "清理残留 agent-flow 进程: $stale"
        echo "$stale" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$stale" | xargs kill -9 2>/dev/null || true
      fi
      info "agent-flow 已停止"
      rm -f "$PID_DIR/flow.pid"
      ;;
    mcp)
      if is_running "$MCP_PORT"; then
        info "停止 MCP Server ..."
        kill_by_port "$MCP_PORT"
        info "MCP Server 已停止"
      else
        info "MCP Server 未在运行"
      fi
      rm -f "$PID_DIR/mcp.pid"
      ;;
    console)
      if command -v pm2 >/dev/null 2>&1 && pm2 id console >/dev/null 2>&1; then
        info "停止 console ..."
        pm2 stop console >/dev/null 2>&1 || true
        info "console 已停止"
      else
        info "console 未在运行"
      fi
      ;;
    *)
      error "未知服务: $svc (可选: fs, asr, tts, mcp, flow, console)"
      ;;
  esac
}

stop_all() {
  info "停止所有服务 ..."
  stop_svc console
  stop_svc flow
  stop_svc mcp
  stop_svc tts
  stop_svc asr
  stop_svc fs
  info "已停止"
}

# ── 启动函数 ──

start_fs() {
  if is_fs_running; then
    info "FreeSWITCH 已在运行"
    return 0
  fi
  # 自愈:macOS 内置 com.apple.ftp-proxy 默认在 launchd 层 socket-activate 占用 8021,
  # 导致 mod_event_socket bind 失败(30s 超时)。netstat 可见监听但 lsof 无主,故用 netstat 检测。
  if netstat -an 2>/dev/null | grep -q '8021.*LISTEN'; then
    warn "8021 已被占用(疑似 launchd 的 ftp-proxy 残留),FS 将无法监听 ESL,尝试释放..."
    if sudo -n launchctl bootout system/com.apple.ftp-proxy 2>/dev/null; then
      sudo -n launchctl disable system/com.apple.ftp-proxy 2>/dev/null || true
      info "已释放 8021 (ftp-proxy bootout + disable)"
      sleep 1
    else
      error "自动释放失败(需 sudo 密码),请手动执行后重试:"
      error "  sudo launchctl bootout system/com.apple.ftp-proxy"
      error "  sudo launchctl disable system/com.apple.ftp-proxy"
      return 1
    fi
  fi
  if [[ ! -x "$FS_BIN" ]]; then
    error "FreeSWITCH 未找到: $FS_BIN"
    return 1
  fi

  info "启动 FreeSWITCH ..."
  "$FS_BIN" -nc -nonat >> "$LOG_DIR/fs.log" 2>&1

  # 等待 ESL 端口就绪
  info "等待 FreeSWITCH 就绪 (ESL port $FS_ESL_PORT) ..."
  for i in $(seq 1 30); do
    if lsof -i:"$FS_ESL_PORT" >/dev/null 2>/dev/null; then
      info "FreeSWITCH 已就绪"
      # 确认 mod_sofia 加载
      sleep 3
      if lsof -i:5060 >/dev/null 2>/dev/null; then
        info "SIP profiles 已就绪 (5060/5080)"
      else
        warn "mod_sofia 可能未加载，尝试手动加载 ..."
        "$HOME/freeswitch/bin/fs_cli" -H 127.0.0.1 -P 8021 -p ClueCon -x "load mod_sofia" 2>/dev/null || true
        sleep 3
      fi
      return 0
    fi
    sleep 1
  done
  warn "FreeSWITCH 在 30s 后未就绪"
  return 1
}

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

  # Mac: cpu 避免 MPS fallback 开销；Linux: auto (CUDA or CPU)
  local tts_device="${COSYVOICE_DEVICE:-cpu}"

  info "启动 TTS (CosyVoice, port $TTS_PORT, device=$tts_device) ..."
  conda run -n cosyvoice bash -c \
    "cd '$PROJECT_DIR/agent-tts/ttsadapter' \
     && export PYTORCH_ENABLE_MPS_FALLBACK=1 \
     && PYTHONPATH='$PROJECT_DIR/agent-tts' \
        MODEL_DIR='$TTS_MODEL_DIR' \
        VOICES_DIR='$VOICES_DIR' \
        TTS_CACHE_DIR='$TTS_CACHE_DIR' \
        COSYVOICE_RUNTIME='$COSYVOICE_RUNTIME' \
        COSYVOICE_DEVICE='$tts_device' \
        uvicorn main:app --host 0.0.0.0 --port $TTS_PORT --ws-ping-interval 120 --ws-ping-timeout 180 \
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
        uvicorn main:app --host 0.0.0.0 --port $FLOW_PORT --ws-ping-interval 86400 --ws-ping-timeout 86400 \
        >> '$LOG_DIR/flow.log' 2>&1" &
  echo $! > "$PID_DIR/flow.pid"
  wait_http "agent-flow" "$FLOW_PORT" 30
}

start_mcp() {
  if port_listening "$MCP_PORT"; then
    info "MCP Server 已在运行 (port $MCP_PORT)"
    return 0
  fi

  local mcp_dir="$PROJECT_DIR/agent-mcp/java-mcp-server"
  if [[ ! -f "$mcp_dir/pom.xml" ]]; then
    error "MCP Server 未找到: $mcp_dir"
    return 1
  fi

  # MCP 需 Java 21（Spring Boot 4.0 + record 语法）。强制使用 brew 的 openjdk@21，
  # 覆盖系统可能存在的旧版 JAVA_HOME（如 Java 8），并兼容 Intel(/usr/local) 与 Apple Silicon(/opt/homebrew)
  local jdk21_home
  jdk21_home="$(brew --prefix openjdk@21 2>/dev/null || true)"
  if [[ -z "$jdk21_home" || ! -x "$jdk21_home/bin/java" ]]; then
    error "未找到 openjdk@21（'brew --prefix openjdk@21' 无效）。请先执行 brew install openjdk@21"
    return 1
  fi
  export JAVA_HOME="$jdk21_home"
  info "启动 MCP Server (port $MCP_PORT, JAVA_HOME=$jdk21_home) ..."
  # 优先使用已构建的 jar，否则 mvnw spring-boot:run
  local jar_path="$mcp_dir/target/mcp-server-0.0.1-SNAPSHOT.jar"
  if [[ -f "$jar_path" ]]; then
    "$jdk21_home/bin/java" -jar "$jar_path" >> "$LOG_DIR/mcp.log" 2>&1 &
  else
    info "jar 未找到，使用 mvnw spring-boot:run ..."
    (cd "$mcp_dir" && ./mvnw spring-boot:run >> "$LOG_DIR/mcp.log" 2>&1) &
  fi
  echo $! > "$PID_DIR/mcp.pid"
  wait_listen "MCP Server" "$MCP_PORT" 60
}

start_console() {
  if port_listening "$CONSOLE_PORT"; then
    info "console 已在运行 (port $CONSOLE_PORT)"
    return 0
  fi
  if [[ ! -f "$CONSOLE_DIR/ecosystem.config.cjs" ]]; then
    error "console 未找到: $CONSOLE_DIR/ecosystem.config.cjs"
    return 1
  fi
  if ! command -v pm2 >/dev/null 2>&1; then
    error "未找到 pm2,请先执行 npm install -g pm2"
    return 1
  fi

  info "启动 console (Next.js dev, port $CONSOLE_PORT, pm2 守护) ..."
  # pm2 start 已存在则 restart,不会报错;应用日志由 ecosystem.config.cjs 写入 console/server/logs/
  (cd "$CONSOLE_DIR" && pm2 start ecosystem.config.cjs) >> "$LOG_DIR/console.log" 2>&1
  wait_listen "console" "$CONSOLE_PORT" 60
}

show_status() {
  echo ""
  printf "${CYAN}%-16s %-10s %-8s %-8s %s${NC}\n" "服务" "引擎" "端口" "状态" "PID"
  printf "%-16s %-10s %-8s %-8s %s\n" "────────────────" "──────" "────" "──────" "─────"

  for row in "FreeSWITCH|SIP/RTP|5060/8021|fs" \
             "ASR|SenseVoice|$ASR_PORT|asr" \
             "TTS|CosyVoice|$TTS_PORT|tts" \
             "MCP Server|Spring Boot|$MCP_PORT|mcp" \
             "agent-flow|LangGraph|$FLOW_PORT|flow" \
             "Console|Next.js|$CONSOLE_PORT|console"; do
    IFS='|' read -r name engine port svc <<< "$row"
    local s="stopped" pid
    pid=$(get_pid "$svc")
    if [[ "$svc" == "fs" ]]; then
      if is_fs_running; then s="running"; fi
    elif [[ "$svc" == "mcp" ]]; then
      # MCP (Spring Boot) 未暴露 /healthz，用 TCP 监听判断
      if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then s="running"; fi
    elif [[ "$svc" == "console" ]]; then
      # console (Next.js dev) 无 /healthz,用 TCP 监听判断;PID 由 pm2 管理
      if port_listening "$port"; then s="running"; fi
      pid="$(pm2 pid console 2>/dev/null || true)"
    elif is_running "$port"; then
      s="running"
    fi
    printf "%-16s %-10s %-8s %-8s %s\n" "$name" "$engine" "$port" "$s" "${pid:--}"
  done
  echo ""
}

# ── 参数解析 ──
# 支持: stop asr | stop flow | stop (停全部) | stop fs asr
SERVICES=()
ACTION="start"

for arg in "$@"; do
  case "$arg" in
    stop)    ACTION="stop" ;;
    status)  ACTION="status" ;;
    -h|--help)
      echo "用法: $0 [stop] [fs|asr|tts|mcp|flow|console] ..."
      echo "  (无参数)         启动全部 (fs asr tts mcp flow console)"
      echo "  fs               仅启动 FreeSWITCH"
      echo "  asr              仅启动 ASR"
      echo "  tts              仅启动 TTS"
      echo "  mcp              仅启动 MCP Server"
      echo "  flow             仅启动 agent-flow"
      echo "  console          仅启动 Console (Next.js, pm2 守护)"
      echo "  stop             停止全部"
      echo "  stop asr         仅停止 ASR"
      echo "  stop console     仅停止 console"
      echo "  stop fs asr      停止 FreeSWITCH + ASR"
      echo "  status           查看状态"
      exit 0 ;;
    *)       SERVICES+=("$arg") ;;
  esac
done

# ── 执行 ──
case "$ACTION" in
  stop)
    if [[ ${#SERVICES[@]} -eq 0 ]]; then
      stop_all
    else
      for svc in "${SERVICES[@]}"; do
        stop_svc "$svc"
      done
    fi
    ;;
  status) show_status ;;
  start)
    info "══════════════════════════════════════"
    info "  本地服务启动"
    info "══════════════════════════════════════"

    # 默认全部
    [[ ${#SERVICES[@]} -eq 0 ]] && SERVICES=(fs asr tts mcp flow console)

    for svc in "${SERVICES[@]}"; do
      case "$svc" in
        fs)      start_fs ;;
        asr)     start_asr ;;
        tts)     start_tts ;;
        mcp)     start_mcp ;;
        flow)    start_flow ;;
        console) start_console ;;
        *)       error "未知服务: $svc (可选: fs, asr, tts, mcp, flow, console)" ;;
      esac
    done

    echo ""
    show_status
    info "日志目录: $LOG_DIR/"
    info "  tail -f $LOG_DIR/fs.log"
    info "  tail -f $LOG_DIR/asr.log"
    info "  tail -f $LOG_DIR/tts.log"
    info "  tail -f $LOG_DIR/mcp.log"
    info "  tail -f $LOG_DIR/flow.log"
    info "  console: pm2 logs console"
    ;;
esac
