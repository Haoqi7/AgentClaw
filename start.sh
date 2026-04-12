#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · V8 一键启动脚本
# 启动全部后台进程：Gateway + 编排引擎 + 数据刷新 + 看板服务器
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log()   { echo -e "${GREEN}[start] $1${NC}"; }
warn()  { echo -e "${YELLOW}[start][WARN] $1${NC}"; }
error() { echo -e "${RED}[start][ERROR] $1${NC}"; }

PIDFILE="$REPO_DIR/.start.pid"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"

# ── 清理函数 ──────────────────────────────────────────────
cleanup() {
  echo ""
  log "收到退出信号，正在停止所有进程..."
  
  # 停止编排引擎
  if [ -n "${ORCH_PID:-}" ] && kill -0 "$ORCH_PID" 2>/dev/null; then
    kill "$ORCH_PID" 2>/dev/null || true
    wait "$ORCH_PID" 2>/dev/null || true
    log "编排引擎已停止 (PID=$ORCH_PID)"
  fi
  
  # 停止 run_loop
  if [ -n "${LOOP_PID:-}" ] && kill -0 "$LOOP_PID" 2>/dev/null; then
    kill "$LOOP_PID" 2>/dev/null || true
    wait "$LOOP_PID" 2>/dev/null || true
    log "数据刷新循环已停止 (PID=$LOOP_PID)"
  fi
  
  # 停止看板服务器
  if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    log "看板服务器已停止 (PID=$SERVER_PID)"
  fi
  
  rm -f "$PIDFILE"
  log "所有进程已停止"
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ── 单实例保护 ──────────────────────────────────────────────
if [ -f "$PIDFILE" ]; then
  OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo -e "${RED}已有 start.sh 实例运行中 (PID=$OLD_PID)${NC}"
    echo "如需重启，请先执行: kill $OLD_PID"
    exit 1
  fi
  rm -f "$PIDFILE"
fi
echo $$ > "$PIDFILE"

DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  AgentClaw V8 一键启动                    ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: 检查 Gateway ──────────────────────────────────
log "检查 OpenClaw Gateway (端口 18789)..."
if (echo > /dev/tcp/127.0.0.1/18789) 2>/dev/null; then
  log "Gateway 已在线"
else
  warn "Gateway 未运行，正在启动..."
  nohup openclaw gateway > "$LOG_DIR/gateway.log" 2>&1 &
  GW_PID=$!
  log "等待 Gateway 启动..."
  for i in $(seq 1 30); do
    if (echo > /dev/tcp/127.0.0.1/18789) 2>/dev/null; then
      log "Gateway 已就绪 (${i}s)"
      break
    fi
    if ! kill -0 "$GW_PID" 2>/dev/null; then
      error "Gateway 启动失败！请先运行: openclaw gateway"
      exit 1
    fi
    sleep 1
  done
  if ! (echo > /dev/tcp/127.0.0.1/18789) 2>/dev/null; then
    error "Gateway 30 秒内未就绪，请检查配置"
    exit 1
  fi
fi

# ── Step 2: 启动编排引擎（V8 核心）─────────────────────────
log "启动编排引擎 pipeline_orchestrator.py..."
python3 "$REPO_DIR/scripts/pipeline_orchestrator.py" > "$LOG_DIR/orchestrator.log" 2>&1 &
ORCH_PID=$!
sleep 1
if kill -0 "$ORCH_PID" 2>/dev/null; then
  log "编排引擎已启动 (PID=$ORCH_PID)"
else
  error "编排引擎启动失败！查看日志: $LOG_DIR/orchestrator.log"
  exit 1
fi

# ── Step 3: 启动数据刷新循环 ──────────────────────────────
if [ -f "$REPO_DIR/scripts/run_loop.sh" ]; then
  log "启动数据刷新循环 run_loop.sh..."
  bash "$REPO_DIR/scripts/run_loop.sh" > "$LOG_DIR/run_loop.log" 2>&1 &
  LOOP_PID=$!
  sleep 1
  if kill -0 "$LOOP_PID" 2>/dev/null; then
    log "数据刷新循环已启动 (PID=$LOOP_PID)"
  else
    warn "数据刷新循环启动失败（非致命，可手动运行: bash scripts/run_loop.sh）"
  fi
else
  warn "未找到 scripts/run_loop.sh，跳过"
fi

# ── Step 4: 前台启动看板服务器 ────────────────────────────
log "启动看板服务器 (端口 $DASHBOARD_PORT)..."
log "打开浏览器访问: http://127.0.0.1:$DASHBOARD_PORT"
echo ""
echo -e "${GREEN}所有进程已启动：${NC}"
echo "  Gateway:       PID=$(pgrep -f 'openclaw gateway' | head -1)"
echo "  编排引擎:      PID=$ORCH_PID"
echo "  数据刷新循环:  PID=${LOOP_PID:-未启动}"
echo "  看板服务器:    即将在前台启动"
echo ""
echo "日志目录: $LOG_DIR/"
echo "按 Ctrl+C 停止所有进程"
echo ""

# 前台运行看板服务器（容器主进程）
python3 "$REPO_DIR/dashboard/server.py" --port "$DASHBOARD_PORT" &
SERVER_PID=$!
wait $SERVER_PID
