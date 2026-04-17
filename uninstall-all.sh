#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# 三省六部 · AgentClaw 一键卸载脚本 (uninstall-all.sh)
# ══════════════════════════════════════════════════════════════════
#
# 功能：清除 install-all.sh 安装的全部内容
#        服务停止 → Agent 清理 → 仓库删除 → 系统依赖卸载（可选）
#
# 用法：
#   bash uninstall-all.sh              # 交互式，逐步确认
#   bash uninstall-all.sh -y           # 全部默认 yes（静默卸载）
#   bash uninstall-all.sh --project-only  # 只清理项目，不动系统依赖
#   bash uninstall-all.sh --nuke       # 全部卸载 + 删 openclaw + 仓库
#
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()   { echo -e "${GREEN}  [OK] $1${NC}"; }
warn()  { echo -e "${YELLOW}  [--] $1${NC}"; }
error() { echo -e "${RED}  [!!] $1${NC}"; }
info()  { echo -e "${BLUE}  [..] $1${NC}"; }
step()  { echo -e "\n${CYAN}${BOLD}━━━ $1 ━━━${NC}"; }

NO_INTERACTIVE=false
PROJECT_ONLY=false
NUKE_MODE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes|--non-interactive) NO_INTERACTIVE=true; shift ;;
    --project-only)             PROJECT_ONLY=true; shift ;;
    --nuke)                     NUKE_MODE=true; NO_INTERACTIVE=true; shift ;;
    -h|--help)                  head -25 "$0" | tail -16; exit 0 ;;
    *)                          error "未知参数: $1"; exit 1 ;;
  esac
done

banner() {
  echo ""
  echo -e "${RED}╔═══════════════════════════════════════════════════╗${NC}"
  echo -e "${RED}║                                                   ║${NC}"
  echo -e "${RED}║     三省六部 · AgentClaw 一键卸载                  ║${NC}"
  echo -e "${RED}║                                                   ║${NC}"
  echo -e "${RED}╚═══════════════════════════════════════════════════╝${NC}"
  echo ""
}

confirm() {
  local prompt="$1"
  if [[ "$NO_INTERACTIVE" == true ]]; then return 0; fi
  echo -ne "${YELLOW}$prompt [Y/n] ${NC}"
  local ans
  read -r ans
  [[ ! "$ans" =~ ^[Nn]$ ]]
}

# 默认路径
INSTALL_DIR="$HOME/AgentClaw"
OC_HOME="$HOME/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"

# 检测实际安装路径
detect_install() {
  # 检查是否有 .install-log.txt 记录安装目录
  for candidate in "$HOME/AgentClaw" "$HOME/agentclaw" "/opt/AgentClaw" "/opt/agentclaw"; do
    if [[ -f "$candidate/.install-log.txt" ]]; then
      INSTALL_DIR="$candidate"
      break
    fi
  done

  # 检查 start.sh 是否在某个目录里
  if [[ ! -d "$INSTALL_DIR" ]]; then
    for candidate in "$HOME/AgentClaw" "$HOME/agentclaw" "$HOME/Desktop/AgentClaw" "$PWD"; do
      if [[ -f "$candidate/start.sh" ]] && [[ -f "$candidate/install.sh" ]]; then
        INSTALL_DIR="$candidate"
        break
      fi
    done
  fi

  info "检测到项目目录: $INSTALL_DIR"
  info "OpenClaw 目录:  $OC_HOME"
}

# ══════════════════════════════════════════════════════════════
# 第 1 步：停止所有服务
# ══════════════════════════════════════════════════════════════
stop_services() {
  step "停止所有服务"

  local stopped=0

  # 1) 用 stop.sh 如果存在
  if [[ -f "$INSTALL_DIR/stop.sh" ]]; then
    info "使用 stop.sh 停止服务..."
    bash "$INSTALL_DIR/stop.sh" 2>/dev/null && stopped=$((stopped+1)) || true
  fi

  # 2) PID 文件兜底
  for pidfile in "$INSTALL_DIR/.gateway.pid" "$INSTALL_DIR/.loop.pid" "$INSTALL_DIR/.dashboard.pid"; do
    if [[ -f "$pidfile" ]]; then
      local pid
      pid=$(cat "$pidfile" 2>/dev/null)
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        stopped=$((stopped+1))
      fi
      rm -f "$pidfile"
    fi
  done

  # 3) 进程名兜底
  for pattern in "run_loop.sh" "dashboard/server.py"; do
    if pgrep -f "$pattern" > /dev/null 2>&1; then
      pkill -f "$pattern" 2>/dev/null || true
      stopped=$((stopped+1))
    fi
  done

  # 4) 检查通用 PID 文件
  for pidfile in "$INSTALL_DIR/sansheng_liubu_refresh.pid" "$HOME/sansheng_liubu_refresh.pid"; do
    if [[ -f "$pidfile" ]]; then
      local pid
      pid=$(cat "$pidfile" 2>/dev/null)
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done

  sleep 1
  log "服务已停止"
}

# ══════════════════════════════════════════════════════════════
# 第 2 步：清除 OpenClaw Agent 配置
# ══════════════════════════════════════════════════════════════
unregister_agents() {
  step "清除 Agent 注册与 Workspace"

  local AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao jiancha)

  # 备份配置
  if [[ -f "$OC_CFG" ]]; then
    cp "$OC_CFG" "$OC_CFG.bak.pre-uninstall-$(date +%Y%m%d-%H%M%S)"
    log "配置已备份: $OC_CFG.bak.*"
  fi

  if ! command -v python3 &>/dev/null; then
    warn "未找到 python3，跳过自动配置清理"
    # 仍删除 workspace 目录
    for agent in "${AGENTS[@]}"; do
      rm -rf "$OC_HOME/workspace-$agent"
    done
    log "已清理 Workspace 目录（配置未修改）"
    return 0
  fi

  python3 << PYEOF
import json, pathlib, sys

cfg_path = pathlib.Path("$OC_CFG")
if not cfg_path.exists():
    print("  openclaw.json 不存在，跳过")
    sys.exit(0)

try:
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
except Exception as e:
    print(f"  解析失败: {e}")
    sys.exit(1)

TO_REMOVE = {"taizi","zhongshu","menxia","shangshu",
             "hubu","libu","bingbu","xingbu","gongbu",
             "libu_hr","zaochao","jiancha"}

agents_list = cfg.get('agents', {}).get('list', [])
before = len(agents_list)
cfg['agents']['list'] = [a for a in agents_list if a.get('id') not in TO_REMOVE]
removed = before - len(cfg['agents']['list'])

cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"  从 openclaw.json 移除了 {removed} 个 Agent")
PYEOF

  # 清理 Workspace 目录
  local removed_ws=0
  for agent in "${AGENTS[@]}"; do
    local ws="$OC_HOME/workspace-$agent"
    if [[ -d "$ws" ]]; then
      rm -rf "$ws"
      removed_ws=$((removed_ws + 1))
    fi
  done
  log "已清理 $removed_ws 个 Workspace 目录"

  # 清理 agents 子目录（API Key 同步产生的）
  for agent in "${AGENTS[@]}"; do
    local agent_dir="$OC_HOME/agents/$agent"
    if [[ -d "$agent_dir" ]]; then
      rm -rf "$agent_dir"
    fi
  done
  log "已清理 Agent 配置目录"
}

# ══════════════════════════════════════════════════════════════
# 第 3 步：删除项目代码
# ══════════════════════════════════════════════════════════════
remove_project() {
  step "删除项目代码"

  if [[ ! -d "$INSTALL_DIR" ]]; then
    warn "项目目录不存在: $INSTALL_DIR"
    return 0
  fi

  if confirm "是否删除整个项目目录 $INSTALL_DIR ？"; then
    rm -rf "$INSTALL_DIR"
    log "项目目录已删除"
  else
    warn "保留项目目录: $INSTALL_DIR"
  fi
}

# ══════════════════════════════════════════════════════════════
# 第 4 步：卸载 OpenClaw（可选）
# ══════════════════════════════════════════════════════════════
remove_openclaw() {
  step "卸载 OpenClaw CLI"

  if ! command -v openclaw &>/dev/null; then
    warn "OpenClaw CLI 未安装，跳过"
    return 0
  fi

  if confirm "是否卸载 OpenClaw CLI？"; then
    npm uninstall -g @qingchencloud/openclaw-zh 2>/dev/null || \
    npm uninstall -g @qingchencloud/openclaw-zh@latest 2>/dev/null || \
    warn "npm 卸载失败，请手动执行: npm uninstall -g @qingchencloud/openclaw-zh"

    # 检查是否还有残留
    if command -v openclaw &>/dev/null; then
      warn "openclaw 命令仍然存在（可能是其他包提供的），请手动检查"
    else
      log "OpenClaw CLI 已卸载"
    fi
  else
    info "保留 OpenClaw CLI"
  fi
}

# ══════════════════════════════════════════════════════════════
# 第 5 步：清理 OpenClaw 数据（可选，--nuke 模式）
# ══════════════════════════════════════════════════════════════
nuke_openclaw_data() {
  step "清理 OpenClaw 全部数据"

  if [[ ! -d "$OC_HOME" ]]; then
    warn "$OC_HOME 不存在，跳过"
    return 0
  fi

  # 检查是否还有其他 Agent（非三省六部的）
  local has_other_agents=false
  if [[ -f "$OC_CFG" ]]; then
    local remaining
    remaining=$(python3 -c "
import json
cfg = json.load(open('$OC_CFG'))
agents = [a['id'] for a in cfg.get('agents',{}).get('list',[])]
sansheng = {'taizi','zhongshu','menxia','shangshu','hubu','libu','bingbu','xingbu','gongbu','libu_hr','zaochao','jiancha'}
other = [a for a in agents if a not in sansheng]
print(len(other))
" 2>/dev/null || echo "0")

    if [[ "$remaining" -gt 0 ]]; then
      has_other_agents=true
      warn "检测到 $remaining 个非三省六部的 Agent 配置"
    fi
  fi

  if confirm "是否删除整个 ~/.openclaw 目录？（包含所有 OpenClaw 配置和数据）"; then
    rm -rf "$OC_HOME"
    log "~/.openclaw 已删除"
  else
    info "保留 ~/.openclaw"
  fi
}

# ══════════════════════════════════════════════════════════════
# 第 6 步：卸载系统依赖（可选）
# ══════════════════════════════════════════════════════════════
remove_system_deps() {
  step "卸载系统依赖（可选）"

  local removed_any=false

  # 检测包管理器
  local PKG_MANAGER=""
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "$ID" in
      ubuntu|debian|linuxmint|pop) PKG_MANAGER="apt" ;;
      centos|rhel|rocky|alma|fedora) PKG_MANAGER="dnf" ;;
      arch|manjaro|endeavouros) PKG_MANAGER="pacman" ;;
      alpine) PKG_MANAGER="apk" ;;
    esac
  elif [[ "$(uname)" == "Darwin" ]]; then
    PKG_MANAGER="brew"
  fi

  # Node.js（通过 nvm 安装的）
  local nvm_path="$HOME/.nvm/nvm.sh"
  if [[ -f "$nvm_path" ]]; then
    if confirm "是否卸载 nvm 和 Node.js？"; then
      rm -rf "$HOME/.nvm"
      # 清理 shell 配置中的 nvm 初始化行
      for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile" "$HOME/.bash_profile"; do
        if [[ -f "$rc" ]]; then
          sed -i '/NVM_DIR/d; /nvm\.sh/d' "$rc" 2>/dev/null || \
          sed -i '' '/NVM_DIR/d; /nvm\.sh/d' "$rc" 2>/dev/null || true
        fi
      done
      log "nvm 已卸载"
      removed_any=true
    fi
  elif [[ "$(uname)" == "Darwin" ]] && brew list node 2>/dev/null; then
    if confirm "是否通过 brew 卸载 Node.js？"; then
      brew uninstall node 2>/dev/null || warn "brew uninstall node 失败"
      removed_any=true
    fi
  fi

  # Python（只卸载通过脚本安装的，不碰系统自带）
  warn "Python 通常为系统自带，不建议卸载（影响范围大）"
  info "如需卸载 Python，请手动处理"

  echo ""
  if [[ "$removed_any" == true ]]; then
    log "系统依赖清理完成"
  else
    info "未卸载任何系统依赖"
  fi
}

# ══════════════════════════════════════════════════════════════
# 总结
# ══════════════════════════════════════════════════════════════
print_summary() {
  echo ""
  echo -e "${RED}╔═══════════════════════════════════════════════════╗${NC}"
  echo -e "${RED}║                                                   ║${NC}"
  echo -e "${RED}║     三省六部 · AgentClaw 卸载完成！               ║${NC}"
  echo -e "${RED}║                                                   ║${NC}"
  echo -e "${RED}╚═══════════════════════════════════════════════════╝${NC}"
  echo ""
  info "剩余项检查："
  echo "─────────────────────────────────────────"

  # OpenClaw
  if command -v openclaw &>/dev/null; then
    echo -e "  ${YELLOW}[--]${NC} OpenClaw CLI 仍在"
  else
    echo -e "  ${GREEN}[OK]${NC} OpenClaw CLI 已卸载"
  fi

  # 项目目录
  if [[ -d "$INSTALL_DIR" ]]; then
    echo -e "  ${YELLOW}[--]${NC} 项目目录仍在: $INSTALL_DIR"
  else
    echo -e "  ${GREEN}[OK]${NC} 项目目录已删除"
  fi

  # Workspace
  local ws_left=0
  for agent in taizi zhongshu menxia shangshu; do
    [[ -d "$OC_HOME/workspace-$agent" ]] && ws_left=$((ws_left+1))
  done
  if [[ "$ws_left" -gt 0 ]]; then
    echo -e "  ${YELLOW}[--]${NC} 部分 Workspace 仍在"
  else
    echo -e "  ${GREEN}[OK]${NC} Agent Workspace 已清理"
  fi

  echo "─────────────────────────────────────────"
  echo ""
  info "如需重新安装，运行："
  echo "  curl -sSL https://raw.githubusercontent.com/Haoqi7/AgentClaw/main/install-all.sh | bash"
  echo ""
}

# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
main() {
  banner

  if [[ "$NUKE_MODE" == true ]]; then
    echo -e "${RED}${BOLD}  ⚠️  NUKE 模式：将清除所有相关内容，不可恢复！${NC}"
    echo ""
  else
    echo -e "${YELLOW}  此脚本将清除 install-all.sh 安装的全部内容${NC}"
    echo -e "${YELLOW}  系统依赖卸载需要逐项确认，不会强制删除${NC}"
    echo ""
  fi

  detect_install

  # 如果只是项目级卸载，跳过系统依赖部分
  if [[ "$PROJECT_ONLY" == true ]]; then
    stop_services
    unregister_agents
    remove_project
    print_summary
    exit 0
  fi

  # 正常流程
  stop_services
  unregister_agents
  remove_project

  # 以下为可选步骤
  remove_openclaw

  if [[ "$NUKE_MODE" == true ]]; then
    nuke_openclaw_data
    remove_system_deps
  else
    if confirm "是否清理 OpenClaw 全部数据 (~/.openclaw)？"; then
      nuke_openclaw_data
    fi
    if confirm "是否卸载通过此脚本安装的系统依赖 (nvm/Node.js)？"; then
      remove_system_deps
    fi
  fi

  print_summary
}

main "$@"
