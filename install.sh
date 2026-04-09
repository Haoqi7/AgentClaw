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

  # 通用 AGENTS.md（工作协议）
  for agent in "${AGENTS[@]}"; do
    cat > "$OC_HOME/workspace-$agent/AGENTS.md" << 'AGENTS_EOF'




# AGENTS.md · 工作协议

## 🔒 身份锚定铁律

**每个 Agent 在处理任何消息之前，必须先执行身份自检：**
1. 明确自己的身份名称和所属层级（监督层/决策层/执行层）
2. 明确自己的直接上级是谁、唯一允许调用的下级是谁

### 身份与层级表

| 部门 | 层级 | 身份名 | 直接上级 | 允许调用的下级 |
|------|------|--------|----------|---------------|
| **太子** | 监督层 | 太子 | 皇上（仅对接） | 仅中书省 |
| **中书省** | 决策层 | 中书令 | 太子 | 门下省、尚书省 |
| **门下省** | 决策层 | 门下侍中 | 中书省 | 无（只审核不转发） |
| **尚书省** | 决策层 | 尚书令 | 中书省 | 六部（工/兵/户/礼/刑/吏） |
| **工部** | 执行层 | 工部尚书 | 尚书省 | 无 |
| **兵部** | 执行层 | 兵部尚书 | 尚书省 | 无 |
| **户部** | 执行层 | 户部尚书 | 尚书省 | 无 |
| **礼部** | 执行层 | 礼部尚书 | 尚书省 | 无 |
| **刑部** | 执行层 | 刑部尚书 | 尚书省 | 无 |
| **吏部** | 执行层 | 吏部尚书 | 尚书省 | 无 |

### 🔒 身份冒充零容忍
- **禁止任何 Agent 自称其他部门**：例如中书省不得说"我是礼部，我来写文档"
- **禁止任何 Agent 代行其他部门职责**：例如中书省不得直接写代码/写文档/做测试
- **禁止跨层级直接调用**：太子不得直接调用六部；中书省不得直接调用六部
- **唯一合法调用链**：太子 → 中书省 → 门下省 → 中书省 → 尚书省 → 六部

---

## 权限说明（严格遵守）

**各部要严格遵守自己的职责，禁止越权。**

### 三省六部职责表

| 部门 | 身份 | 职责 | 禁止事项 |
|------|------|------|----------|
| **太子** | 监督 | 接收皇上消息、分拣任务、创建任务、转交中书省、持续监督全流程、向皇上汇报最终结果 | ❌ 禁止跳过任何环节、禁止让子会话直接与皇上对话、禁止直接调用六部、禁止执行任何实际任务 |
| **中书省** | 决策 | 起草方案、提交审议、转交执行 | ❌ 禁止直接执行任何具体工作、禁止跳过门下省审核、禁止直接与皇上对话、禁止直接调用六部 |
| **门下省** | 决策 | 审议方案、准奏/封驳 | ❌ 禁止执行任务、禁止修改方案 |
| **尚书省** | 决策 | 派发六部、汇总结果 | ❌ 禁止越权代劳六部工作、禁止跳过六部自行执行 |
| **工部** | 执行 | 部署运维、安全防御、漏洞扫描、定时任务 | ❌ 禁止承接非本职工作 |
| **兵部** | 执行 | 功能开发、架构设计、代码实现 | ❌ 禁止承接非本职工作 |
| **户部** | 执行 | 数据分析、统计报表、成本核算、数据相关 | ❌ 禁止承接非本职工作 |
| **刑部** | 执行 | 代码审查、测试验收、合规审计 | ❌ 禁止承接非本职工作 |
| **礼部** | 执行 | 文档撰写、UI/UX、对外沟通、撰写文案 | ❌ 禁止承接非本职工作 |
| **吏部** | 执行 | 人事管理、Agent培训 | ❌ 禁止承接技术执行工作 |

---

## 📡 强制接旨确认协议

**这是整个流程的基石，违反此协议 = 流程断裂。**

### 1. 接旨必须做的事（所有 Agent，无一例外）
当收到上级部门通过 `sessions_spawn` 发来的任务时，**第一件事必须是**：
```
sessions_send --to [上级部门] "已收到 JJC-xxx [任务标题]，[本部门身份名]开始执行"
```
**然后**才能开始自己的工作。不回复确认 = 未接旨 = 流程未完成。

### 2. 上级必须做的事
- 上级在**收到下级的"已收到"确认之前**，不得将自己的步骤标记为完成
- 如果下级 **5 分钟**未回复确认 → 发送催办消息
- 催办后仍 **5 分钟**无响应 → 上报太子

### 3. 完成后必须做的事
完成任务后，**必须**使用 `sessions_send` 向上级汇报结果：
```
sessions_send --to [上级部门] "✅ 完成 JJC-xxx：[产出摘要]"
```

---

## 📡 工作流程

1. 除太子外各部接到**任务**必须先回复上级部门"已接旨"（见强制接旨确认协议）。
2. 完成任务后必须向上级部门汇报。
3. **【文件存放规范】各部需在自己工作区内自行新建一个文件夹，将任务创建的文件放入其中，不得将文件直接存放于工作区根目录。**

---

## 📡 Subagent 调用规则（静默模式 - 统一标准）

### ⚠️ 调用模式铁律

**所有部门调用下级时，必须严格遵守以下规则：**

1. **必须使用静默模式：**
   ```json
   {
     "mode": "run",
     "thread": false
   }
   ```

2. **禁止使用会话模式：**
   ```json
   // ❌ 绝对禁止
   {
     "mode": "session",
     "thread": true
   }
   ```

3. **原因：**
   - `mode: "session"` 会创建持久会话，导致会话爆炸
   - `thread: true` 会尝试绑定飞书线程，在非飞书环境下会失败
   - 静默模式 (`mode: "run"`) 执行完自动销毁，不会残留

4. **违规后果：**
   - 会话数量失控
   - 资源浪费
   - 状态混乱难以追踪

### 调用方式
- **首次调用某个部门** → 使用 `sessions_spawn`
- **继续已有对话** → 使用 `sessions_send`
- ❌ **禁止用 `sessions_yield` 调用 subagent**（会返回 `{"status": "yielded"}`，子部门不会执行）

### sessions_spawn 标准格式（静默模式）
```json
{
  "agentId": "目标部门ID",
  "task": "处理任务 JJC-xxx：核心需求一句话概括",
  "mode": "run",
  "thread": false
}
```

**参数说明：**
| 参数 | 值 | 说明 |
|------|-----|------|
| `agentId` | 目标部门ID | 如 `zhongshu`、`shangshu`、`gongbu` 等 |
| `task` | 纯文本字符串 | **不要包含换行符 `\n`**，只写核心摘要 |
| `mode` | `"run"` | 一次性任务，执行完自动销毁 |
| `thread` | `false` | 不绑定飞书线程，子Agent在后台静默执行 |

### 调用流程（两步走）

**第一步：用 `sessions_spawn` 创建子Agent**
```json
{
  "agentId": "zhongshu",
  "task": "处理任务 JJC-20260407-001：审查项目健康度",
  "mode": "run",
  "thread": false
}
```

**第二步：从返回结果获取 `sessionKey`，用 `sessions_send` 发送详细任务内容**
```json
{
  "sessionKey": "agent:zhongshu:subagent:xxx-xxx-xxx",
  "message": "📋 任务详情\n任务ID: JJC-20260407-001\n目标：全面审查项目\n要求：\n  - 检查代码质量\n  - 审查安全漏洞\n  - 输出审查报告"
}
```

### 重要提醒
- `task` 字段**绝对不要包含换行符 `\n`**
- 详细内容通过 `sessions_send` 单独发送
- `mode: "run"` + `thread: false` = 子Agent后台静默执行，不会出现在飞书聊天中
- 子Agent执行完成后，结果会自动沿调用链反向回传

---

## 结果回传铁律
- 任务执行完成后，**禁止直接输出结果**
- 禁止擅自向皇上汇报，只有太子能向皇上汇报最终结果
- 所有结果必须沿调用链反向回传：六部 → 尚书省 → 中书省 → 太子 → 皇上



AGENTS_EOF
  done
}

# ── Step 2: 注册 Agents ─────────────────────────────────────
register_agents() {
  info "注册三省六部 Agents..."

  # 备份配置
  cp "$OC_CFG" "$OC_CFG.bak.sansheng-$(date +%Y%m%d-%H%M%S)"
  log "已备份配置: $OC_CFG.bak.*"

  python3 << 'PYEOF'
import json, pathlib, sys

cfg_path = pathlib.Path.home() / '.openclaw' / 'openclaw.json'
cfg = json.loads(cfg_path.read_text())

AGENTS = [
  {"id": "taizi",    "subagents": {"allowAgents": ["zhongshu"]}},
    {"id": "zhongshu", "subagents": {"allowAgents": ["menxia", "shangshu"]}},
    {"id": "menxia",   "subagents": {"allowAgents": ["zhongshu"]}},
  {"id": "shangshu", "subagents": {"allowAgents": ["zhongshu", "hubu", "libu", "bingbu", "xingbu", "gongbu", "libu_hr"]}},
    {"id": "hubu",     "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "libu",     "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "bingbu",   "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "xingbu",   "subagents": {"allowAgents": ["shangshu"]}},
    {"id": "gongbu",   "subagents": {"allowAgents": ["shangshu"]}},
  {"id": "libu_hr",  "subagents": {"allowAgents": ["shangshu"]}},
  {"id": "zaochao",  "subagents": {"allowAgents": []}},
  {"id": "jiancha",  "subagents": {"allowAgents": ["taizi","zhongshu","menxia","shangshu","hubu","libu","bingbu","xingbu","gongbu","libu_hr"]}},
]

agents_cfg = cfg.setdefault('agents', {})
agents_list = agents_cfg.get('list', [])
existing_ids = {a['id'] for a in agents_list}

added = 0
for ag in AGENTS:
    ag_id = ag['id']
    ws = str(pathlib.Path.home() / f'.openclaw/workspace-{ag_id}')
    if ag_id not in existing_ids:
        entry = {'id': ag_id, 'workspace': ws, **{k:v for k,v in ag.items() if k!='id'}}
        agents_list.append(entry)
        added += 1
        print(f'  + added: {ag_id}')
    else:
        print(f'  ~ exists: {ag_id} (skipped)')

agents_cfg['list'] = agents_list

# Fix #142: 清理 bindings 中的非法字段（pattern 不被 gateway 支持）
bindings = cfg.get('bindings', [])
cleaned = 0
for b in bindings:
    match = b.get('match', {})
    if isinstance(match, dict) and 'pattern' in match:
        del match['pattern']
        cleaned += 1
        print(f'  🧹 cleaned invalid "pattern" from binding: {b.get("agentId", "?")}')
if cleaned:
    print(f'Cleaned {cleaned} invalid binding field(s)')

cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
print(f'Done: {added} agents added')
PYEOF

  log "Agents 注册完成"
}

# ── Step 3: 初始化 Data ─────────────────────────────────────
init_data() {
  info "初始化数据目录..."
  
  mkdir -p "$REPO_DIR/data"
  
  # 初始化空文件
  for f in live_status.json agent_config.json model_change_log.json; do
    if [ ! -f "$REPO_DIR/data/$f" ]; then
      echo '{}' > "$REPO_DIR/data/$f"
    fi
  done
  echo '[]' > "$REPO_DIR/data/pending_model_changes.json"

  # 初始任务文件
  if [ ! -f "$REPO_DIR/data/tasks_source.json" ]; then
    python3 << 'PYEOF'
import json, pathlib
tasks = [
    {
        "id": "JJC-DEMO-001",
        "title": "🎉 系统初始化完成",
        "official": "工部尚书",
        "org": "工部",
        "state": "Done",
        "now": "三省六部系统已就绪",
        "eta": "-",
        "block": "无",
        "output": "",
        "ac": "系统正常运行",
        "flow_log": [
            {"at": "2024-01-01T00:00:00Z", "from": "皇上", "to": "中书省", "remark": "下旨初始化三省六部系统"},
            {"at": "2024-01-01T00:01:00Z", "from": "中书省", "to": "门下省", "remark": "规划方案提交审核"},
            {"at": "2024-01-01T00:02:00Z", "from": "门下省", "to": "尚书省", "remark": "✅ 准奏"},
            {"at": "2024-01-01T00:03:00Z", "from": "尚书省", "to": "工部", "remark": "派发：系统初始化"},
            {"at": "2024-01-01T00:04:00Z", "from": "工部", "to": "尚书省", "remark": "✅ 完成"},
        ]
    }
]
import os
data_dir = pathlib.Path(os.environ.get('REPO_DIR', '.')) / 'data'
data_dir.mkdir(exist_ok=True)
(data_dir / 'tasks_source.json').write_text(json.dumps(tasks, ensure_ascii=False, indent=2))
print('tasks_source.json 已初始化')
PYEOF
  fi

  log "数据目录初始化完成: $REPO_DIR/data"
}

# ── Step 3.3: 创建 data 软链接确保数据一致 (Fix #88) ─────────
link_resources() {
  info "创建 data/scripts 软链接以确保 Agent 数据一致..."
  
  AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao jiancha)
  LINKED=0
  for agent in "${AGENTS[@]}"; do
    ws="$OC_HOME/workspace-$agent"
    mkdir -p "$ws"

    # 软链接 data 目录：确保各 agent 读写同一份 tasks_source.json
    ws_data="$ws/data"
    if [ -L "$ws_data" ]; then
      : # 已是软链接，跳过
    elif [ -d "$ws_data" ]; then
      # 已有 data 目录（非符号链接），备份后替换
      mv "$ws_data" "${ws_data}.bak.$(date +%Y%m%d-%H%M%S)"
      ln -s "$REPO_DIR/data" "$ws_data"
      LINKED=$((LINKED + 1))
    else
      ln -s "$REPO_DIR/data" "$ws_data"
      LINKED=$((LINKED + 1))
    fi

    # 软链接 scripts 目录
    ws_scripts="$ws/scripts"
    if [ -L "$ws_scripts" ]; then
      : # 已是软链接
    elif [ -d "$ws_scripts" ]; then
      mv "$ws_scripts" "${ws_scripts}.bak.$(date +%Y%m%d-%H%M%S)"
      ln -s "$REPO_DIR/scripts" "$ws_scripts"
      LINKED=$((LINKED + 1))
    else
      ln -s "$REPO_DIR/scripts" "$ws_scripts"
      LINKED=$((LINKED + 1))
    fi
  done

  # Legacy: workspace-main
  ws_main="$OC_HOME/workspace-main"
  if [ -d "$ws_main" ]; then
    for target in data scripts; do
      link_path="$ws_main/$target"
      if [ ! -L "$link_path" ]; then
        [ -d "$link_path" ] && mv "$link_path" "${link_path}.bak.$(date +%Y%m%d-%H%M%S)"
        ln -s "$REPO_DIR/$target" "$link_path"
        LINKED=$((LINKED + 1))
      fi
    done
  fi

  log "已创建 $LINKED 个软链接（data/scripts → 项目目录）"
}

# ── Step 3.5: 设置 Agent 间通信可见性 (Fix #83 + 方案 A 会话隔离) ──────────────
setup_visibility() {
  info "配置 Agent 间消息可见性..."
  # 🔧 方案 A 修复：改为 own（会话隔离）
  # 旧值：all — Agent 可以看到所有其他 Agent 的会话，导致子代理可能跨会话通信
  # 新值：own — Agent 只能看到自己创建的会话，实现会话隔离
  #   子代理只能与创建它的主会话通信，无法访问其他 Agent 的 sessionKey
  if openclaw config set tools.sessions.visibility own 2>/dev/null; then
    log "已设置 tools.sessions.visibility=own（会话隔离：Agent 只能访问自己创建的会话）"
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
    warn "请安装 Node.js 18+ 后运行: cd edict/frontend && npm install && npm run build"
    return
  fi

  if [ -f "$REPO_DIR/edict/frontend/package.json" ]; then
    cd "$REPO_DIR/edict/frontend"
    npm install --silent 2>/dev/null || npm install
    npm run build 2>/dev/null
    cd "$REPO_DIR"
    if [ -f "$REPO_DIR/dashboard/dist/index.html" ]; then
      log "前端构建完成: dashboard/dist/"
    else
      warn "前端构建可能失败，请手动检查"
    fi
  else
    warn "未找到 edict/frontend/package.json，跳过前端构建"
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
info "文档: docs/getting-started.md"
