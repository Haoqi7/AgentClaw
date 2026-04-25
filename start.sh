#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 一键启动脚本（本地 Linux 部署）
#
# 用法:
#   bash start.sh           # 默认启动
#   bash start.sh --detach  # 后台运行（日志写入 start.log）
#   bash stop.sh            # 停止所有服务
# ══════════════════════════════════════════════════════════════
set -euo pipefail

# ── 路径与常量 ──────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OC_HOME="$HOME/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"
GATEWAY_PORT=18789
DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"

# 导出环境变量（所有子进程继承）
export EDICT_HOME="$REPO_DIR"
export EDICT_DASHBOARD_PORT="$DASHBOARD_PORT"

# PID 文件
GATEWAY_PID_FILE="$REPO_DIR/.gateway.pid"
LOOP_PID_FILE="$REPO_DIR/.loop.pid"
DASHBOARD_PID_FILE="$REPO_DIR/.dashboard.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log()  { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }

banner() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  🏛️  三省六部 · 一键启动                   ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""
}

# ── 前置检查 ──────────────────────────────────────────────
check_prerequisites() {
  info "检查运行环境..."

  # 1. openclaw CLI
  if ! command -v openclaw &>/dev/null; then
    error "未找到 openclaw CLI，请先安装："
    echo "    npm install -g openclaw@latest"
    echo "    openclaw onboard --install-daemon"
    exit 1
  fi
  log "OpenClaw CLI: $(openclaw --version 2>/dev/null || echo '已安装')"

  # 2. python3
  if ! command -v python3 &>/dev/null; then
    error "未找到 python3，请安装 Python 3.10+"
    exit 1
  fi
  log "Python3: $(python3 --version)"

  # 3. openclaw.json 是否存在
  if [ ! -f "$OC_CFG" ]; then
    error "未找到 openclaw.json，请先运行初始化："
    echo "    openclaw onboard --install-daemon"
    echo "    openclaw init"
    exit 1
  fi
  log "openclaw.json: $OC_CFG"

  # 4. install.sh 是否已运行过（检查 workspace 和 data 是否存在）
  if [ ! -d "$REPO_DIR/data" ] || [ ! -d "$OC_HOME/workspace-taizi" ]; then
    error "检测到尚未完成安装，请先运行："
    echo "    chmod +x install.sh && ./install.sh"
    exit 1
  fi
  log "安装状态: 已安装"

  # 5. 检查端口占用
  if (echo > /dev/tcp/127.0.0.1/$GATEWAY_PORT) 2>/dev/null; then
    warn "端口 $GATEWAY_PORT 已被占用（Gateway 可能已在运行）"
  fi
  if (echo > /dev/tcp/127.0.0.1/$DASHBOARD_PORT) 2>/dev/null; then
    warn "端口 $DASHBOARD_PORT 已被占用（看板可能已在运行）"
  fi
}

# ── 单实例保护 ────────────────────────────────────────────
check_single_instance() {
  local pid_files=( "$GATEWAY_PID_FILE" "$LOOP_PID_FILE" "$DASHBOARD_PID_FILE" )
  local running=0

  for pf in "${pid_files[@]}"; do
    if [ -f "$pf" ]; then
      pid=$(cat "$pf" 2>/dev/null)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        running=$((running + 1))
      else
        rm -f "$pf"
      fi
    fi
  done

  if [ "$running" -gt 0 ]; then
    error "检测到 $running 个服务正在运行，请先执行 bash stop.sh 停止"
    echo "    或者执行 bash stop.sh && bash start.sh 重启"
    exit 1
  fi
}

# ── 优雅退出处理 ──────────────────────────────────────────
cleanup() {
  echo ""
  warn "收到退出信号，正在停止所有服务..."

  # 停止 dashboard（前台进程由 exec 启动，信号直接传递）
  # 停止 gateway
  if [ -f "$GATEWAY_PID_FILE" ]; then
    gw_pid=$(cat "$GATEWAY_PID_FILE" 2>/dev/null)
    if [ -n "$gw_pid" ] && kill -0 "$gw_pid" 2>/dev/null; then
      kill "$gw_pid" 2>/dev/null || true
      wait "$gw_pid" 2>/dev/null || true
      log "Gateway (PID=$gw_pid) 已停止"
    fi
    rm -f "$GATEWAY_PID_FILE"
  fi

  # 停止 run_loop
  if [ -f "$LOOP_PID_FILE" ]; then
    loop_pid=$(cat "$LOOP_PID_FILE" 2>/dev/null)
    if [ -n "$loop_pid" ] && kill -0 "$loop_pid" 2>/dev/null; then
      kill "$loop_pid" 2>/dev/null || true
      wait "$loop_pid" 2>/dev/null || true
      log "数据刷新循环 (PID=$loop_pid) 已停止"
    fi
    rm -f "$LOOP_PID_FILE"
  fi

  rm -f "$DASHBOARD_PID_FILE"
  echo -e "${YELLOW}所有服务已停止${NC}"
}

# ── 启动 Gateway ──────────────────────────────────────────
start_gateway() {
  info "启动 OpenClaw Gateway (端口 $GATEWAY_PORT)..."

  openclaw gateway &
  local pid=$!
  echo "$pid" > "$GATEWAY_PID_FILE"

  # 等待 Gateway 就绪（最多 60 秒）
  echo -n "   "
  for i in $(seq 1 60); do
    if (echo > /dev/tcp/127.0.0.1/$GATEWAY_PORT) 2>/dev/null; then
      echo ""
      log "Gateway 已就绪 (PID=$pid)"
      return 0
    fi
    # 检查进程是否已退出
    if ! kill -0 "$pid" 2>/dev/null; then
      echo ""
      error "Gateway 进程意外退出！请检查配置："
      echo "    1. 运行 openclaw doctor --fix"
      echo "    2. 检查 $OC_CFG 中的 API Key 和渠道配置"
      exit 1
    fi
    echo -n "░"
    sleep 1
  done

  echo ""
  error "Gateway 未在 60 秒内就绪，请检查配置后重试"
  exit 1
}

# ── 启动数据刷新循环 ──────────────────────────────────────
start_loop() {
  info "启动数据刷新循环..."

  if [ ! -f "$REPO_DIR/scripts/run_loop.sh" ]; then
    error "未找到 scripts/run_loop.sh"
    exit 1
  fi

  bash "$REPO_DIR/scripts/run_loop.sh" &
  local pid=$!
  echo "$pid" > "$LOOP_PID_FILE"
  log "数据刷新循环已启动 (PID=$pid, 间隔 15s)"
}

# ── 启动看板服务器 ────────────────────────────────────────
start_dashboard() {
  info "启动看板服务器 (端口 $DASHBOARD_PORT)..."

  if [ ! -f "$REPO_DIR/dashboard/server.py" ]; then
    error "未找到 dashboard/server.py"
    exit 1
  fi

  # 检查 dist 是否存在（前端构建产物）
  if [ ! -f "$REPO_DIR/dashboard/dist/index.html" ]; then
    warn "未找到前端构建产物，看板页面可能无法正常显示"
    warn "如需构建前端，执行: cd frontend && npm install && npm run build"
  fi

  log "看板地址: http://127.0.0.1:$DASHBOARD_PORT"
}

# ── 完整启动流程 ──────────────────────────────────────────
detach_mode=false
for arg in "$@"; do
  case "$arg" in
    --detach|-d) detach_mode=true ;;
    --help|-h)
      echo "用法: bash start.sh [--detach]"
      echo "  无参数    前台运行（Ctrl+C 停止所有服务）"
      echo "  --detach  后台运行（日志写入 start.log，用 bash stop.sh 停止）"
      exit 0
      ;;
  esac
done

banner
check_prerequisites
check_single_instance

echo -e "${BLUE}── 启动服务 ──${NC}"
echo ""

start_gateway
start_loop
start_dashboard

echo ""
echo -e "${BLUE}── 启动完成 ──${NC}"
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  🏛️  三省六部系统已启动！                       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  📊 看板地址:  http://127.0.0.1:$DASHBOARD_PORT"
echo "  🔄 数据刷新:  每 15 秒同步"
echo "  🛡️ 监察巡检:  每 60 秒扫描"
echo ""
echo "  停止服务:  bash stop.sh"
echo "  查看日志:  tail -f $REPO_DIR/data/sansheng_liubu_refresh.log"
echo ""

# 注册退出清理
trap cleanup SIGINT SIGTERM EXIT

if [ "$detach_mode" = true ]; then
  # 后台模式：dashboard 也在后台，日志写入 start.log
  info "后台模式：所有服务已在后台运行"
  info "日志文件: $REPO_DIR/start.log"
  python3 "$REPO_DIR/dashboard/server.py" --port "$DASHBOARD_PORT" >> "$REPO_DIR/start.log" 2>&1 &
  echo "$!" > "$DASHBOARD_PID_FILE"
  log "看板服务器 (PID=$!)，日志: start.log"

  # 解除 trap，让脚本正常退出
  trap - SIGINT SIGTERM EXIT
else
  # 前台模式：dashboard 在前台运行，Ctrl+C 触发 cleanup
  exec python3 "$REPO_DIR/dashboard/server.py" --port "$DASHBOARD_PORT"
fi
