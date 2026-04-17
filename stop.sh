#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 一键停止脚本
#
# 用法: bash stop.sh
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GATEWAY_PID_FILE="$REPO_DIR/.gateway.pid"
LOOP_PID_FILE="$REPO_DIR/.loop.pid"
DASHBOARD_PID_FILE="$REPO_DIR/.dashboard.pid"
LOOP_PIDFILE="$REPO_DIR/data/sansheng_liubu_refresh.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }

echo -e "${YELLOW}🏛️  停止三省六部服务...${NC}"
echo ""

stopped=0

# ── 停止 Gateway ──────────────────────────────────────────
if [ -f "$GATEWAY_PID_FILE" ]; then
  pid=$(cat "$GATEWAY_PID_FILE" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    sleep 1
    # 强制终止
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    log "Gateway (PID=$pid) 已停止"
    stopped=$((stopped + 1))
  else
    warn "Gateway PID 文件存在但进程已退出"
  fi
  rm -f "$GATEWAY_PID_FILE"
else
  # 兜底：通过进程名查找
  if pgrep -f "openclaw gateway" > /dev/null 2>&1; then
    pkill -f "openclaw gateway" 2>/dev/null || true
    log "Gateway (进程匹配) 已停止"
    stopped=$((stopped + 1))
  fi
fi

# ── 停止数据刷新循环 ──────────────────────────────────────
if [ -f "$LOOP_PID_FILE" ]; then
  pid=$(cat "$LOOP_PID_FILE" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    log "数据刷新循环 (PID=$pid) 已停止"
    stopped=$((stopped + 1))
  else
    warn "数据刷新循环 PID 文件存在但进程已退出"
  fi
  rm -f "$LOOP_PID_FILE"
else
  # 兜底：run_loop.sh 自身的 PIDFILE
  if [ -f "$LOOP_PIDFILE" ]; then
    pid=$(cat "$LOOP_PIDFILE" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      log "数据刷新循环 (PID=$pid) 已停止"
      stopped=$((stopped + 1))
    fi
    rm -f "$LOOP_PIDFILE"
  fi
fi

# ── 停止看板服务器 ────────────────────────────────────────
if [ -f "$DASHBOARD_PID_FILE" ]; then
  pid=$(cat "$DASHBOARD_PID_FILE" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    log "看板服务器 (PID=$pid) 已停止"
    stopped=$((stopped + 1))
  else
    warn "看板服务器 PID 文件存在但进程已退出"
  fi
  rm -f "$DASHBOARD_PID_FILE"
else
  # 兜底：通过进程名查找
  if pgrep -f "python.*dashboard/server.py" > /dev/null 2>&1; then
    pkill -f "python.*dashboard/server.py" 2>/dev/null || true
    log "看板服务器 (进程匹配) 已停止"
    stopped=$((stopped + 1))
  fi
fi

# ── 清理残留 PID 文件 ─────────────────────────────────────
rm -f "$GATEWAY_PID_FILE" "$LOOP_PID_FILE" "$DASHBOARD_PID_FILE"

echo ""
if [ "$stopped" -gt 0 ]; then
  echo -e "${GREEN}已停止 $stopped 个服务${NC}"
else
  warn "没有检测到正在运行的服务"
fi

echo "重新启动: bash start.sh"
