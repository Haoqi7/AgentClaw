#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · OpenClaw Multi-Agent System 一键安装脚本
# ══════════════════════════════════════════════════════════════
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OC_HOME="$HOME/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

banner() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  🏛️  三省六部 · OpenClaw Multi-Agent    ║${NC}"
  echo -e "${BLUE}║       安装向导                            ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""
}

log()   { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }
info()  { echo -e "${BLUE}ℹ️  $1${NC}"; }

# ── Step 0: 依赖检查 ──────────────────────────────────────────
check_deps() {
  info "检查依赖..."
  
  if ! command -v openclaw &>/dev/null; then
    error "未找到 openclaw CLI。请先安装 OpenClaw: https://openclaw.ai"
    exit 1
  fi
  log "OpenClaw CLI: $(openclaw --version 2>/dev/null || echo 'OK')"

  if ! command -v python3 &>/dev/null; then
    error "未找到 python3"
    exit 1
  fi
  log "Python3: $(python3 --version)"

  if [ ! -f "$OC_CFG" ]; then
    error "未找到 openclaw.json。请先运行 openclaw 完成初始化。"
    exit 1
  fi
  log "openclaw.json: $OC_CFG"
}

# ── Step 0.5: 备份已有 Agent 数据 ──────────────────────────────
backup_existing() {
  AGENTS_DIR="$OC_HOME"
  BACKUP_DIR="$OC_HOME/backups/pre-install-$(date +%Y%m%d-%H%M%S)"
  HAS_EXISTING=false

  # 检查是否有已存在的 workspace
  for d in "$AGENTS_DIR"/workspace-*/; do
    if [ -d "$d" ]; then
      HAS_EXISTING=true
      break
    fi
  done

  if $HAS_EXISTING; then
    info "检测到已有 Agent Workspace，自动备份中..."
    mkdir -p "$BACKUP_DIR"

    # 备份所有 workspace 目录
    for d in "$AGENTS_DIR"/workspace-*/; do
      if [ -d "$d" ]; then
        ws_name=$(basename "$d")
        cp -R "$d" "$BACKUP_DIR/$ws_name"
      fi
    done

    # 备份 openclaw.json
    if [ -f "$OC_CFG" ]; then
      cp "$OC_CFG" "$BACKUP_DIR/openclaw.json"
    fi

    # 备份 agents 目录（agent 注册信息）
    if [ -d "$AGENTS_DIR/agents" ]; then
      cp -R "$AGENTS_DIR/agents" "$BACKUP_DIR/agents"
    fi

    log "已备份到: $BACKUP_DIR"
    info "如需恢复，运行: cp -R $BACKUP_DIR/workspace-* $AGENTS_DIR/"
  fi
}

# ── Step 1: 创建 Workspace ──────────────────────────────────
create_workspaces() {
  info "创建 Agent Workspace..."
  
  AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao jiancha)
  for agent in "${AGENTS[@]}"; do
    ws="$OC_HOME/workspace-$agent"
    mkdir -p "$ws/skills"
    if [ -f "$REPO_DIR/agents/$agent/SOUL.md" ]; then
      if [ -f "$ws/SOUL.md" ]; then
        # 已存在的 SOUL.md，先备份再覆盖
        cp "$ws/SOUL.md" "$ws/SOUL.md.bak.$(date +%Y%m%d-%H%M%S)"
        warn "已备份旧 SOUL.md → $ws/SOUL.md.bak.*"
      fi
      sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/agents/$agent/SOUL.md" > "$ws/SOUL.md"
    fi
    log "Workspace 已创建: $ws"
  done

  # 从 agents/$agent/AGENTS.md 文件夹复制工作协议（与 SOUL.md 一致）
  for agent in "${AGENTS[@]}"; do
    if [ -f "$REPO_DIR/agents/$agent/AGENTS.md" ]; then
      if [ -f "$OC_HOME/workspace-$agent/AGENTS.md" ]; then
        cp "$OC_HOME/workspace-$agent/AGENTS.md" "$OC_HOME/workspace-$agent/AGENTS.md.bak.$(date +%Y%m%d-%H%M%S)"
        warn "已备份旧 AGENTS.md → $OC_HOME/workspace-$agent/AGENTS.md.bak.*"
      fi
      cp "$REPO_DIR/agents/$agent/AGENTS.md" "$OC_HOME/workspace-$agent/AGENTS.md"
      log "AGENTS.md 已复制: $OC_HOME/workspace-$agent/AGENTS.md"
    else
      warn "未找到 $REPO_DIR/agents/$agent/AGENTS.md，跳过"
    fi
  done

  # 从 agents/$agent/IDENTITY.md 文件夹复制身份定义
  for agent in "${AGENTS[@]}"; do
    if [ -f "$REPO_DIR/agents/$agent/IDENTITY.md" ]; then
      if [ -f "$OC_HOME/workspace-$agent/IDENTITY.md" ]; then
        cp "$OC_HOME/workspace-$agent/IDENTITY.md" "$OC_HOME/workspace-$agent/IDENTITY.md.bak.$(date +%Y%m%d-%H%M%S)"
        warn "已备份旧 IDENTITY.md → $OC_HOME/workspace-$agent/IDENTITY.md.bak.*"
      fi
      cp "$REPO_DIR/agents/$agent/IDENTITY.md" "$OC_HOME/workspace-$agent/IDENTITY.md"
      log "IDENTITY.md 已复制: $OC_HOME/workspace-$agent/IDENTITY.md"
    else
      warn "未找到 $REPO_DIR/agents/$agent/IDENTITY.md，跳过"
    fi
  done
}

# ── Step 2: 注册 Agents ─────────────────────────────────────
register_agents() {
  info "注册三省六部 Agents..."

  # 备份配置
  cp "$OC_CFG" "$OC_CFG.bak.sansheng-$(date +%Y%m%d-%H%M%S)"
  log "已备份配置: $OC_CFG.bak.*"

  python3  "$REPO_DIR/data/$f"
    fi
  done
  echo '[]' > "$REPO_DIR/data/pending_model_changes.json"

  # 初始任务文件
  if [ ! -f "$REPO_DIR/data/tasks_source.json" ]; then
    python3 /dev/null; then
    log "已设置 tools.sessions.visibility=own（会话隔离模式：Agent 仅可见自己的会话）"
  else
    warn "设置 visibility 失败（可能 openclaw 版本不支持），请手动执行:"
    echo "    openclaw config set tools.sessions.visibility own"
  fi
}

# ── Step 3.5b: 同步 API Key 到所有 Agent ──────────────────────────
sync_auth() {
  info "同步 API Key 到所有 Agent..."

  # OpenClaw ≥ 3.13 stores credentials in models.json; older versions use
  # auth-profiles.json. Try the new name first, then fall back to the old one.
  MAIN_AUTH=""
  AUTH_FILENAME=""
  AGENT_BASE="$OC_HOME/agents/main/agent"

  for candidate in models.json auth-profiles.json; do
    if [ -f "$AGENT_BASE/$candidate" ]; then
      MAIN_AUTH="$AGENT_BASE/$candidate"
      AUTH_FILENAME="$candidate"
      break
    fi
  done

  # Fallback: search across all agents for either filename
  if [ -z "$MAIN_AUTH" ]; then
    for candidate in models.json auth-profiles.json; do
      MAIN_AUTH=$(find "$OC_HOME/agents" -name "$candidate" -maxdepth 3 2>/dev/null | head -1)
      if [ -n "$MAIN_AUTH" ] && [ -f "$MAIN_AUTH" ]; then
        AUTH_FILENAME="$candidate"
        break
      fi
      MAIN_AUTH=""
    done
  fi

  if [ -z "$MAIN_AUTH" ] || [ ! -f "$MAIN_AUTH" ]; then
    warn "未找到已有的 models.json 或 auth-profiles.json"
    warn "请先为任意 Agent 配置 API Key:"
    echo "    openclaw agents add taizi"
    echo "  然后重新运行 install.sh，或手动执行:"
    echo "    bash install.sh --sync-auth"
    return
  fi

  # 检查文件内容是否有效（非空 JSON）
  if ! python3 -c "import json; d=json.load(open('$MAIN_AUTH')); assert d" 2>/dev/null; then
    warn "$AUTH_FILENAME 为空或无效，请先配置 API Key:"
    echo "    openclaw agents add taizi"
    return
  fi

  AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao jiancha)
  SYNCED=0
  for agent in "${AGENTS[@]}"; do
    AGENT_DIR="$OC_HOME/agents/$agent/agent"
    if [ -d "$AGENT_DIR" ] || mkdir -p "$AGENT_DIR" 2>/dev/null; then
      cp "$MAIN_AUTH" "$AGENT_DIR/$AUTH_FILENAME"
      SYNCED=$((SYNCED + 1))
    fi
  done

  log "API Key 已同步到 $SYNCED 个 Agent"
  info "来源: $MAIN_AUTH"
}

# ── Step 4: 构建前端 ──────────────────────────────────────────
build_frontend() {
  info "构建 React 前端..."

  if ! command -v node &>/dev/null; then
    warn "未找到 node，跳过前端构建。看板将使用预构建版本（如果存在）"
    warn "请安装 Node.js 22+ 后运行: cd frontend && npm install && npm run build"
    return
  fi

  if [ -f "$REPO_DIR/frontend/package.json" ]; then
    cd "$REPO_DIR/frontend"
    npm install --silent 2>/dev/null || npm install
    npm run build 2>/dev/null
    cd "$REPO_DIR"
    if [ -f "$REPO_DIR/dashboard/dist/index.html" ]; then
      log "前端构建完成: dashboard/dist/"
    else
      warn "前端构建可能失败，请手动检查"
    fi
  else
    warn "未找到 frontend/package.json，跳过前端构建"
  fi
}

# ── Step 5: 首次数据同步 ────────────────────────────────────
first_sync() {
  info "执行首次数据同步..."
  cd "$REPO_DIR"
  
  REPO_DIR="$REPO_DIR" python3 scripts/sync_agent_config.py || warn "sync_agent_config 有警告"
  python3 scripts/sync_officials_stats.py || warn "sync_officials_stats 有警告"
  python3 scripts/refresh_live_data.py || warn "refresh_live_data 有警告"
  
  log "首次同步完成"
}

# ── Step 6: 重启 Gateway ────────────────────────────────────
restart_gateway() {
  info "重启 OpenClaw Gateway..."
  if openclaw gateway restart 2>/dev/null; then
    log "Gateway 重启成功"
  else
    warn "Gateway 重启失败，请手动重启：openclaw gateway restart"
  fi
}

# ── Main ────────────────────────────────────────────────────
banner
check_deps
backup_existing
create_workspaces
register_agents
init_data
link_resources
setup_visibility
sync_auth
build_frontend
first_sync
restart_gateway

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  🎉  三省六部安装完成！                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "下一步："
echo "  1. 配置 API Key（如尚未配置）:"
echo "     openclaw agents add taizi     # 按提示输入 Anthropic API Key"
echo "     ./install.sh                  # 重新运行以同步到所有 Agent"
echo "  2. 启动数据刷新循环:  bash scripts/run_loop.sh &"
echo "  3. 启动看板服务器:    python3 \"\$REPO_DIR/dashboard/server.py\""
echo "  4. 打开看板:          http://127.0.0.1:7891"
echo ""
warn "首次安装必须配置 API Key，否则 Agent 会报错"
info "文档: readme.md"