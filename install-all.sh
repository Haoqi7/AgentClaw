#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# 三省六部 · AgentClaw 一键安装脚本 (install-all.sh)
# ══════════════════════════════════════════════════════════════════
#
# 功能：在裸机 Linux / macOS / WSL 上从零安装 AgentClaw 全套环境
# 用法：
#   curl -sSL https://raw.githubusercontent.com/Haoqi7/AgentClaw/main/install-all.sh | bash
#   # 或下载后执行
#   wget https://raw.githubusercontent.com/Haoqi7/AgentClaw/main/install-all.sh
#   bash install-all.sh
#   # 指定安装目录（默认 ~/AgentClaw）
#   bash install-all.sh --dir /opt/AgentClaw
#   # 跳过前端构建（无 Node.js 时自动跳过，也可强制跳过）
#   bash install-all.sh --skip-frontend
#   # 仅安装系统依赖，不克隆仓库/不运行 install.sh
#   bash install-all.sh --deps-only
#   # 安装后自动启动服务
#   bash install-all.sh --auto-start
#
# 支持：Ubuntu/Debian, CentOS/RHEL/Fedora, Arch Linux, macOS, WSL
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 颜色与输出 ──────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()   { echo -e "${GREEN}  [OK] $1${NC}"; }
warn()  { echo -e "${YELLOW}  [--] $1${NC}"; }
error() { echo -e "${RED}  [!!] $1${NC}"; }
info()  { echo -e "${BLUE}  [..] $1${NC}"; }
step()  { echo -e "\n${CYAN}${BOLD}━━━ $1 ━━━${NC}"; }
banner() {
  echo ""
  echo -e "${BLUE}╔═══════════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║                                                   ║${NC}"
  echo -e "${BLUE}║     三省六部 · AgentClaw 一键安装                  ║${NC}"
  echo -e "${BLUE}║     Multi-Agent Collaboration System              ║${NC}"
  echo -e "${BLUE}║                                                   ║${NC}"
  echo -e "${BLUE}║     从零到运行，一条命令搞定                       ║${NC}"
  echo -e "${BLUE}║                                                   ║${NC}"
  echo -e "${BLUE}╚═══════════════════════════════════════════════════╝${NC}"
  echo ""
}

# ── 参数解析 ────────────────────────────────────────────────
INSTALL_DIR=""
SKIP_FRONTEND=false
DEPS_ONLY=false
AUTO_START=false
NO_INTERACTIVE=false
OPENCLAW_PKG="@qingchencloud/openclaw-zh@latest"
REPO_URL="https://github.com/Haoqi7/AgentClaw.git"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      INSTALL_DIR="$2"; shift 2 ;;
    --skip-frontend)
      SKIP_FRONTEND=true; shift ;;
    --deps-only)
      DEPS_ONLY=true; shift ;;
    --auto-start)
      AUTO_START=true; shift ;;
    -y|--yes|--non-interactive)
      NO_INTERACTIVE=true; shift ;;
    -h|--help)
      head -30 "$0" | tail -20
      exit 0 ;;
    *)
      error "未知参数: $1"
      exit 1 ;;
  esac
done

# 默认安装到用户主目录
INSTALL_DIR="${INSTALL_DIR:-$HOME/AgentClaw}"

# ══════════════════════════════════════════════════════════════
# 阶段 0：系统检测
# ══════════════════════════════════════════════════════════════
detect_os() {
  OS_TYPE="unknown"
  OS_DISTRO=""
  PKG_MANAGER=""
  IS_WSL=false
  SUDO_CMD=""

  # 检测 WSL
  if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
  fi
  # macOS
  if [[ "$(uname)" == "Darwin" ]]; then
    OS_TYPE="macos"
    PKG_MANAGER="brew"
  # Linux
  elif [[ "$(uname)" == "Linux" ]]; then
    OS_TYPE="linux"
    if [[ -f /etc/os-release ]]; then
      . /etc/os-release
      OS_DISTRO="$ID"
      case "$ID" in
        ubuntu|debian|linuxmint|pop)
          PKG_MANAGER="apt" ;;
        centos|rhel|rocky|alma|fedora)
          PKG_MANAGER="dnf" ;;
        arch|manjaro|endeavouros)
          PKG_MANAGER="pacman" ;;
        alpine)
          PKG_MANAGER="apk" ;;
        *)
          PKG_MANAGER="unknown" ;;
      esac
    fi
  fi

  # 检测 sudo 权限
  if [[ $EUID -ne 0 ]]; then
    if command -v sudo &>/dev/null; then
      SUDO_CMD="sudo"
    fi
  fi
}

print_system_info() {
  info "操作系统:   $(uname -s) $(uname -r)"
  if [[ -n "$OS_DISTRO" ]]; then
    info "发行版:     $OS_DISTRO"
  fi
  if [[ "$IS_WSL" == true ]]; then
    info "WSL 环境:   是 (Windows Subsystem for Linux)"
  fi
  info "包管理器:   ${PKG_MANAGER:-无}"
  info "安装目录:   $INSTALL_DIR"
  info "用户:       $(whoami)"
  [[ -n "$SUDO_CMD" ]] && info "sudo:       $SUDO_CMD"
  echo ""
}

# ══════════════════════════════════════════════════════════════
# 阶段 1：包管理器工具函数
# ══════════════════════════════════════════════════════════════

# 更新包索引（各发行版）
pkg_update() {
  info "更新软件包索引..."
  case "$PKG_MANAGER" in
    apt)   $SUDO_CMD apt-get update -qq ;;
    dnf)   $SUDO_CMD dnf check-update -q || true ;;
    pacman) $SUDO_CMD pacman -Sy --noconfirm ;;
    apk)   $SUDO_CMD apk update ;;
    brew)  brew update ;;
    *)     warn "未知包管理器，跳过更新" ;;
  esac
}

# 安装一个或多个包
pkg_install() {
  for pkg in "$@"; do
    # 先检查是否已安装
    if command -v "$pkg" &>/dev/null; then
      info "$pkg 已安装，跳过"
      continue
    fi
    # apt: 包名可能与命令名不同，做映射
    local pkg_name="$pkg"
    case "$PKG_MANAGER" in
      apt)
        case "$pkg" in
          python3)  pkg_name="python3" ;;
          node)     pkg_name="nodejs" ;;
          git)      pkg_name="git" ;;
          curl)     pkg_name="curl" ;;
          wget)     pkg_name="wget" ;;
          jq)       pkg_name="jq" ;;
        esac ;;
    esac
    info "正在安装 $pkg ($pkg_name)..."
    case "$PKG_MANAGER" in
      apt)    $SUDO_CMD apt-get install -y -qq "$pkg_name" ;;
      dnf)    $SUDO_CMD dnf install -y -q "$pkg_name" ;;
      pacman) $SUDO_CMD pacman -S --noconfirm --needed "$pkg_name" ;;
      apk)    $SUDO_CMD apk add "$pkg_name" ;;
      brew)   brew install "$pkg_name" ;;
      *)      warn "未知包管理器，无法安装 $pkg" ;;
    esac
  done
}

# ══════════════════════════════════════════════════════════════
# 阶段 2：依赖安装
# ══════════════════════════════════════════════════════════════

# 检查并安装 Git
ensure_git() {
  step "检查 Git"
  if command -v git &>/dev/null; then
    log "Git $(git --version | awk '{print $3}')"
    return 0
  fi
  info "未检测到 Git，正在安装..."
  pkg_update
  case "$PKG_MANAGER" in
    apt)    $SUDO_CMD apt-get install -y -qq git ;;
    dnf)    $SUDO_CMD dnf install -y -q git ;;
    pacman) $SUDO_CMD pacman -S --noconfirm git ;;
    apk)    $SUDO_CMD apk add git ;;
    brew)   brew install git ;;
    *)      error "无法自动安装 Git，请手动安装后重试"; exit 1 ;;
  esac
  command -v git &>/dev/null && log "Git $(git --version | awk '{print $3}')" || { error "Git 安装失败"; exit 1; }
}

# 检查并安装 Python 3.10+
ensure_python() {
  step "检查 Python 3.10+"
  local py_cmd=""

  # 按优先级寻找可用的 python
  for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
      local ver
      ver=$($cmd -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
      local major minor
      major=$(echo "$ver" | cut -d. -f1)
      minor=$(echo "$ver" | cut -d. -f2)
      if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
        py_cmd="$cmd"
        log "Python $ver ($cmd)"
        break
      fi
    fi
  done

  if [[ -n "$py_cmd" ]]; then
    # 确保 python3 命令指向合法版本
    if ! command -v python3 &>/dev/null || \
       [[ "$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null)" -lt 10 ]]; then
      $SUDO_CMD ln -sf "$(command -v "$py_cmd")" /usr/local/bin/python3 2>/dev/null || true
      warn "python3 命令已指向 $py_cmd"
    fi
    return 0
  fi

  info "未检测到 Python 3.10+，正在安装..."

  pkg_update
  case "$PKG_MANAGER" in
    apt)
      $SUDO_CMD apt-get install -y -qq software-properties-common || true
      $SUDO_CMD add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || {
        warn "无法添加 PPA（可能在非 Ubuntu 系统），尝试系统自带版本..."
        $SUDO_CMD apt-get install -y python3 python3-pip
      }
      $SUDO_CMD apt-get install -y -qq python3.12 python3.12-venv python3-pip 2>/dev/null || \
      $SUDO_CMD apt-get install -y -qq python3.11 python3.11-venv python3-pip 2>/dev/null || \
      $SUDO_CMD apt-get install -y -qq python3 python3-pip
      # 确保 python3 指向新版本
      for v in 12 11; do
        if command -v "python3.$v" &>/dev/null; then
          $SUDO_CMD update-alternatives --install /usr/bin/python3 python3 "$(command -v python3.$v)" 1 2>/dev/null || true
          break
        fi
      done
      ;;
    dnf)
      $SUDO_CMD dnf install -y python3.12 python3-pip 2>/dev/null || \
      $SUDO_CMD dnf install -y python3 python3-pip
      ;;
    pacman)
      $SUDO_CMD pacman -S --noconfirm python python-pip
      ;;
    apk)
      $SUDO_CMD apk add python3 py3-pip
      ;;
    brew)
      brew install python@3.12 2>/dev/null || brew install python
      ;;
    *)
      error "无法自动安装 Python，请手动安装 Python 3.10+ 后重试"
      exit 1 ;;
  esac

  # 验证
  local py_ver=""
  py_ver=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "0.0")
  if [[ "$py_ver" == "0.0" ]]; then
    error "Python 安装失败，请手动安装 Python 3.10+ 后重试"
    exit 1
  fi
  log "Python $py_ver 安装完成"
}

# 检查并安装 Node.js 22+
ensure_node() {
  step "检查 Node.js 22+"
  local node_ver=""

  if command -v node &>/dev/null; then
    local ver_str
    ver_str=$(node -v 2>/dev/null | sed 's/^v//')
    local major
    major=$(echo "$ver_str" | cut -d. -f1)
    if [[ "$major" -ge 22 ]]; then
      log "Node.js v$ver_str"
      return 0
    else
      warn "Node.js v$ver_str 版本过低（需要 >= 22）"
    fi
  fi

  info "正在安装 Node.js 22+..."

  # 优先尝试 nvm（最推荐的方式，不需要 sudo，自动管理版本）
  local nvm_path="$HOME/.nvm/nvm.sh"
  if [[ -s "$nvm_path" ]]; then
    info "检测到 nvm，使用 nvm 安装 Node.js..."
    export NVM_DIR="$HOME/.nvm"
    source "$nvm_path"
    nvm install 22
    nvm use 22
    nvm alias default 22
    log "Node.js $(node -v) (via nvm)"
    return 0
  fi

  # 尝试 fnm（更快的 Node 版本管理器）
  if command -v fnm &>/dev/null; then
    info "检测到 fnm，使用 fnm 安装 Node.js..."
    eval "$(fnm env)"
    fnm install 22
    fnm use 22
    fnm default 22
    log "Node.js $(node -v) (via fnm)"
    return 0
  fi

  # 没有 nvm/fnm 时自动安装 nvm
  if [[ "$NO_INTERACTIVE" == true ]] || confirm "是否安装 nvm 来管理 Node.js 版本？(推荐)"; then
    info "安装 nvm..."
    export NVM_DIR="$HOME/.nvm"
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh 2>/dev/null | bash || {
      # 备用地址
      wget -qO- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh 2>/dev/null | bash
    }
    # 加载 nvm
    if [[ -s "$nvm_path" ]]; then
      source "$nvm_path"
      nvm install 22
      nvm use 22
      nvm alias default 22
      log "Node.js $(node -v) (via nvm)"
      return 0
    fi
    warn "nvm 安装失败，尝试系统包管理器安装..."
  fi

  # 兜底：系统包管理器
  pkg_update
  case "$PKG_MANAGER" in
    apt)
      # Ubuntu 24.04+ 自带 Node.js 22，其他版本需要 NodeSource
      $SUDO_CMD apt-get install -y -qq ca-certificates curl gnupg 2>/dev/null || true
      local setup_src="https://deb.nodesource.com/setup_22.x"
      if curl -fsSL "$setup_src" 2>/dev/null | $SUDO_CMD bash -; then
        $SUDO_CMD apt-get install -y -qq nodejs
      else
        warn "NodeSource 添加失败，尝试系统默认版本..."
        $SUDO_CMD apt-get install -y nodejs npm
      fi
      ;;
    dnf)
      $SUDO_CMD dnf module enable nodejs:22 -y 2>/dev/null || true
      $SUDO_CMD dnf install -y nodejs npm 2>/dev/null || \
      $SUDO_CMD dnf install -y nodejs
      ;;
    pacman)
      $SUDO_CMD pacman -S --noconfirm nodejs npm
      ;;
    apk)
      $SUDO_CMD apk add nodejs npm
      ;;
    brew)
      brew install node@22 2>/dev/null || brew install node
      ;;
    *)
      warn "未知包管理器，跳过 Node.js 安装"
      warn "请手动安装 Node.js 22+: https://nodejs.org/"
      return 1 ;;
  esac

  if command -v node &>/dev/null; then
    local final_ver
    final_ver=$(node -v)
    log "Node.js $final_ver 安装完成"
  else
    warn "Node.js 安装可能失败（前端构建将跳过，不影响核心功能）"
  fi
}

# ══════════════════════════════════════════════════════════════
# 阶段 3：OpenClaw 安装
# ══════════════════════════════════════════════════════════════
ensure_npm() {
  step "检查 npm"
  if command -v npm &>/dev/null; then
    log "npm $(npm -v)"
    return 0
  fi

  # npm 通常随 Node.js 一起安装，但某些系统可能分离
  info "未检测到 npm，尝试安装..."
  pkg_update
  case "$PKG_MANAGER" in
    apt)    $SUDO_CMD apt-get install -y -qq npm ;;
    dnf)    $SUDO_CMD dnf install -y npm ;;
    pacman) $SUDO_CMD pacman -S --noconfirm npm ;;
    brew)   brew install npm ;;
    *)      warn "无法安装 npm" ;;
  esac

  command -v npm &>/dev/null && log "npm $(npm -v)" || { error "npm 安装失败"; exit 1; }
}

install_openclaw() {
  step "检查 OpenClaw CLI"
  if command -v openclaw &>/dev/null; then
    local oc_ver
    oc_ver=$(openclaw --version 2>/dev/null || echo "已安装")
    log "OpenClaw 已安装: $oc_ver"
    if [[ "$NO_INTERACTIVE" == false ]]; then
      if confirm "是否重新安装/更新 OpenClaw？"; then
        info "正在更新 OpenClaw..."
      else
        return 0
      fi
    fi
  fi

  info "正在安装 OpenClaw..."
  npm install -g "$OPENCLAW_PKG" || {
    error "OpenClaw 安装失败！"
    info "请检查网络连接，或手动执行："
    info "  npm install -g $OPENCLAW_PKG"
    exit 1
  }
  log "OpenClaw 安装完成"

  # 初始化 OpenClaw（如果尚未初始化）
  step "初始化 OpenClaw"
  local oc_home="$HOME/.openclaw"
  local oc_cfg="$oc_home/openclaw.json"

  if [[ -f "$oc_cfg" ]]; then
    log "OpenClaw 已初始化: $oc_cfg"
  else
    info "正在初始化 OpenClaw..."
    # onboard 会引导用户设置（首次需要交互，但 --install-daemon 可以非交互安装 daemon）
    if [[ "$NO_INTERACTIVE" == true ]]; then
      # 非交互模式：尝试自动初始化
      openclaw onboard --install-daemon 2>/dev/null || {
        warn "自动初始化失败（可能需要交互式配置 API Key）"
        warn "请稍后手动运行: openclaw onboard --install-daemon"
      }
      openclaw init 2>/dev/null || {
        # 创建最小配置
        mkdir -p "$oc_home"
        cat > "$oc_cfg" << 'OCJSON'
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "lan"
  },
  "agents": {
    "defaults": {},
    "list": []
  },
  "session": { "dmScope": "per-channel-peer" },
  "tools": {
    "profile": "full",
    "web": { "search": { "provider": "tavily", "enabled": true } },
    "sessions": { "visibility": "own" },
    "env": { "TZ": "Asia/Shanghai" },
    "agentToAgent": { "enabled": true }
  }
}
OCJSON
        warn "已创建最小 openclaw.json，请稍后通过 openclaw 命令完善配置"
      }
    else
      openclaw onboard --install-daemon || true
      openclaw init || true
    fi

    if [[ -f "$oc_cfg" ]]; then
      log "OpenClaw 初始化完成"
    else
      warn "OpenClaw 初始化未完成，install.sh 可能需要手动处理"
    fi
  fi
}

# ══════════════════════════════════════════════════════════════
# 阶段 4：克隆仓库
# ══════════════════════════════════════════════════════════════
clone_repo() {
  step "获取 AgentClaw 代码"

  if [[ -d "$INSTALL_DIR" ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
      info "仓库已存在: $INSTALL_DIR"
      info "拉取最新代码..."
      git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "git pull 失败，使用本地代码继续"
      return 0
    else
      warn "目录 $INSTALL_DIR 已存在但不是 Git 仓库"
      if [[ "$NO_INTERACTIVE" == false ]] && confirm "是否删除该目录并重新克隆？"; then
        rm -rf "$INSTALL_DIR"
      else
        warn "跳过克隆，请在正确目录执行 install.sh"
        return 0
      fi
    fi
  fi

  info "正在克隆仓库到 $INSTALL_DIR ..."
  git clone "$REPO_URL" "$INSTALL_DIR" || {
    # GitHub 可能被墙，提供备用方案
    warn "GitHub 克隆可能失败（网络问题），尝试备用方案..."
    info "尝试浅克隆 (--depth 1) 以减少下载量..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" || {
      error "仓库克隆失败！请检查网络连接"
      info "备用方案："
      info "  1. 使用代理: export https_proxy=http://your-proxy:port"
      info "  2. 使用镜像: git clone https://gitee.com/mirror/AgentClaw.git $INSTALL_DIR"
      info "  3. 手动下载 ZIP: https://github.com/Haoqi7/AgentClaw/archive/refs/heads/main.zip"
      exit 1
    }
  }
  log "仓库克隆完成: $INSTALL_DIR"
}

# ══════════════════════════════════════════════════════════════
# 阶段 5：运行项目安装脚本
# ══════════════════════════════════════════════════════════════
run_project_install() {
  step "运行项目安装脚本 (install.sh)"

  cd "$INSTALL_DIR"

  if [[ ! -f "install.sh" ]]; then
    error "未找到 install.sh，请确认仓库结构完整"
    exit 1
  fi

  chmod +x install.sh

  # 运行 install.sh
  if [[ "$SKIP_FRONTEND" == true ]]; then
    info "跳过前端构建（--skip-frontend 模式）"
    # install.sh 内部的前端构建会因无 node 而自动跳过
    EDICT_HOME="$INSTALL_DIR" bash install.sh || {
      warn "install.sh 执行有警告，但核心组件应已安装"
    }
  else
    EDICT_HOME="$INSTALL_DIR" bash install.sh || {
      warn "install.sh 执行有警告，但核心组件应已安装"
    }
  fi

  log "项目安装完成"
}

# ══════════════════════════════════════════════════════════════
# 阶段 6：启动服务
# ══════════════════════════════════════════════════════════════
auto_start_services() {
  step "启动服务"

  cd "$INSTALL_DIR"

  if [[ -f "start.sh" ]]; then
    chmod +x start.sh stop.sh 2>/dev/null || true
    export EDICT_HOME="$INSTALL_DIR"
    info "使用 start.sh 启动服务..."
    if [[ "$AUTO_START" == true ]]; then
      info "以 --detach 模式后台启动..."
      bash start.sh --detach
      sleep 3
      echo ""
      echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
      echo -e "${GREEN}║  🎉  三省六部已启动！                            ║${NC}"
      echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
      echo ""
      echo "  看板地址:    http://127.0.0.1:${EDICT_DASHBOARD_PORT:-7891}"
      echo "  Gateway:     http://127.0.0.1:18789"
      echo ""
      echo "  停止服务:    cd $INSTALL_DIR && bash stop.sh"
      echo "  查看日志:    cd $INSTALL_DIR && tail -f start.log"
      echo ""
    else
      info "你可以稍后手动启动："
      echo "  cd $INSTALL_DIR"
      echo "  bash start.sh              # 前台运行"
      echo "  bash start.sh --detach     # 后台运行"
    fi
  else
    warn "未找到 start.sh，请手动启动服务："
    echo "  1. openclaw gateway &"
    echo "  2. bash scripts/run_loop.sh &"
    echo "  3. python3 dashboard/server.py"
  fi
}

# ══════════════════════════════════════════════════════════════
# 阶段 7：最终检查与输出
# ══════════════════════════════════════════════════════════════
final_check() {
  step "安装验证"

  local all_ok=true
  local oc_cfg="$HOME/.openclaw/openclaw.json"

  echo ""

  # 检查项
  echo -e "${BOLD}安装验证清单：${NC}"
  echo "─────────────────────────────────────────"

  # 1. Python
  if command -v python3 &>/dev/null; then
    local py_ver
    py_ver=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if [[ "$(echo "$py_ver" | cut -d. -f1)" -eq 3 && "$(echo "$py_ver" | cut -d. -f2)" -ge 10 ]]; then
      echo -e "  ${GREEN}[OK]${NC} Python $py_ver"
    else
      echo -e "  ${YELLOW}[--]${NC} Python $py_ver (建议 3.10+)"
    fi
  else
    echo -e "  ${RED}[!!]${NC} Python 未安装"
    all_ok=false
  fi

  # 2. Node.js
  if command -v node &>/dev/null; then
    local node_ver
    node_ver=$(node -v | sed 's/^v//')
    local node_major
    node_major=$(echo "$node_ver" | cut -d. -f1)
    if [[ "$node_major" -ge 22 ]]; then
      echo -e "  ${GREEN}[OK]${NC} Node.js v$node_ver"
    else
      echo -e "  ${YELLOW}[--]${NC} Node.js v$node_ver (建议 22+)"
    fi
  else
    echo -e "  ${YELLOW}[--]${NC} Node.js 未安装（前端构建不可用，不影响核心功能）"
  fi

  # 3. OpenClaw
  if command -v openclaw &>/dev/null; then
    echo -e "  ${GREEN}[OK]${NC} OpenClaw CLI"
  else
    echo -e "  ${RED}[!!]${NC} OpenClaw CLI 未安装"
    all_ok=false
  fi

  # 4. openclaw.json
  if [[ -f "$oc_cfg" ]]; then
    echo -e "  ${GREEN}[OK]${NC} openclaw.json ($oc_cfg)"
  else
    echo -e "  ${RED}[!!]${NC} openclaw.json 不存在"
    all_ok=false
  fi

  # 5. Agent Workspace
  local ws_count=0
  for agent in taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao jiancha; do
    if [[ -d "$HOME/.openclaw/workspace-$agent" ]]; then
      ws_count=$((ws_count + 1))
    fi
  done
  if [[ "$ws_count" -eq 12 ]]; then
    echo -e "  ${GREEN}[OK]${NC} 12 个 Agent Workspace 已创建"
  elif [[ "$ws_count" -gt 0 ]]; then
    echo -e "  ${YELLOW}[--]${NC} $ws_count/12 个 Agent Workspace"
  else
    echo -e "  ${RED}[!!]${NC} Agent Workspace 未创建"
    all_ok=false
  fi

  # 6. 数据目录
  if [[ -d "$INSTALL_DIR/data" ]]; then
    echo -e "  ${GREEN}[OK]${NC} 数据目录 ($INSTALL_DIR/data)"
  else
    echo -e "  ${YELLOW}[--]${NC} 数据目录不存在"
  fi

  # 7. 前端构建
  if [[ -f "$INSTALL_DIR/dashboard/dist/index.html" ]]; then
    echo -e "  ${GREEN}[OK]${NC} 前端已构建"
  else
    echo -e "  ${YELLOW}[--]${NC} 前端未构建（需 Node.js 22+，或看板使用预构建版本）"
  fi

  # 8. API Key
  if [[ -f "$oc_cfg" ]]; then
    local has_key=false
    # 检查是否配置了非示例 API Key
    if python3 -c "
import json, sys
cfg = json.load(open('$oc_cfg'))
models = cfg.get('models', {}).get('providers', {})
for name, prov in models.items():
    key = prov.get('apiKey', '')
    if key and key not in ('yours-key', 'yourstokem', 'yours', ''):
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
      echo -e "  ${GREEN}[OK]${NC} API Key 已配置"
    else
      echo -e "  ${YELLOW}[--]${NC} API Key 未配置（请运行: openclaw agents add taizi）"
      all_ok=false
    fi
  fi

  echo "─────────────────────────────────────────"
  echo ""
}

print_next_steps() {
  echo -e "${BOLD}接下来的步骤：${NC}"
  echo ""
  echo "  1️⃣  配置 API Key（首次安装必须）："
  echo "      openclaw agents add taizi"
  echo "      # 按提示输入你的 Anthropic/OpenAI API Key"
  echo "      # 然后重新同步: cd $INSTALL_DIR && bash install.sh"
  echo ""
  echo "  2️⃣  配置消息渠道（选择其一）："
  echo "      openclaw channels add --type feishu --agent taizi     # 飞书"
  echo "      openclaw channels add --type telegram --agent taizi   # Telegram"
  echo "      openclaw channels add --type slack --agent taizi      # Slack"
  echo "      openclaw channels add --type discord --agent taizi    # Discord"
  echo "      openclaw channels add --type wecom --agent taizi      # 企业微信"
  echo "      openclaw channels add --type webhook --agent taizi    # Webhook"
  echo ""
  echo "  3️⃣  启动服务："
  echo "      cd $INSTALL_DIR"
  echo "      bash start.sh              # 前台运行（推荐首次使用）"
  echo "      bash start.sh --detach     # 后台运行"
  echo "      bash stop.sh               # 停止所有服务"
  echo ""
  echo "  4️⃣  打开看板："
  echo "      http://127.0.0.1:7891"
  echo ""
  echo -e "${CYAN}通过消息渠道发送任务指令，观察 12 位「官员」自动协作！${NC}"
  echo ""
  echo -e "${BOLD}常用命令速查：${NC}"
  echo "  openclaw gateway restart    # 重启 Gateway"
  echo "  openclaw doctor --fix       # 诊断修复配置"
  echo "  openclaw status             # 查看状态"
  echo "  bash $INSTALL_DIR/install.sh    # 重新安装/更新 Agent"
  echo ""
  echo -e "${BOLD}文档：${NC} $INSTALL_DIR/README.md"
  echo ""
}

# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

# 交互式确认
confirm() {
  local prompt="$1"
  if [[ "$NO_INTERACTIVE" == true ]]; then
    return 0  # 非交互模式默认 yes
  fi
  echo -ne "${YELLOW}$prompt [Y/n] ${NC}"
  local ans
  read -r ans
  case "$ans" in
    n|N|no|No|NO) return 1 ;;
    *) return 0 ;;
  esac
}

# 记录安装信息（供后续调试）
save_install_log() {
  local log_file="$INSTALL_DIR/.install-log.txt"
  cat > "$log_file" << LOGEOF
AgentClaw 一键安装日志
======================
时间: $(date -Iseconds 2>/dev/null || date)
操作系统: $(uname -s) $(uname -r)
发行版: ${OS_DISTRO:-unknown}
WSL: $IS_WSL
安装目录: $INSTALL_DIR
Python: $(python3 --version 2>/dev/null || echo "未安装")
Node.js: $(node -v 2>/dev/null || echo "未安装")
npm: $(npm -v 2>/dev/null || echo "未安装")
OpenClaw: $(openclaw --version 2>/dev/null || echo "未安装")
LOGEOF
  log "安装日志已保存: $log_file"
}

# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════
main() {
  banner
  detect_os
  print_system_info

  # 阶段 1-3：安装系统依赖
  ensure_git
  ensure_python
  ensure_node
  ensure_npm

  # 阶段 4：安装 OpenClaw
  install_openclaw

  if [[ "$DEPS_ONLY" == true ]]; then
    echo ""
    log "仅安装依赖模式完成（--deps-only），跳过仓库克隆和项目安装"
    echo ""
    exit 0
  fi

  # 阶段 5：克隆仓库
  clone_repo

  # 阶段 6：运行项目安装脚本
  run_project_install

  # 阶段 7：启动服务（可选）
  if [[ "$AUTO_START" == true ]]; then
    auto_start_services
  fi

  # 最终验证
  final_check
  print_next_steps
  save_install_log

  echo -e "${GREEN}╔═══════════════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║                                                   ║${NC}"
  echo -e "${GREEN}║     三省六部 · AgentClaw 安装完成！               ║${NC}"
  echo -e "${GREEN}║                                                   ║${NC}"
  echo -e "${GREEN}╚═══════════════════════════════════════════════════╝${NC}"
  echo ""
}

main "$@"
