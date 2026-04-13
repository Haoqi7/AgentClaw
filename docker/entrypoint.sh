#!/usr/bin/env bash
set -euo pipefail

OC_HOME="/root/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"
INITIALIZED_MARKER="$OC_HOME/.initialized"

log()  { echo "[entrypoint] $*"; }
warn() { echo "[entrypoint][WARN] $*"; }

log "starting..."

# ── 阶段 1：初始化（仅首次）─────────────────────────────────
# 使用幂等标记确保重启时不重复执行全量安装
log "=== 初始化阶段 ==="
if [ ! -f "$INITIALIZED_MARKER" ]; then
  log "首次启动：运行 openclaw onboard/init..."
  mkdir -p "$OC_HOME"
  openclaw onboard --install-daemon || true
  openclaw init || true

  # 运行项目安装脚本（仅首次）
  if [ -f /app/AgentClaw/install.sh ]; then
    cd /app/AgentClaw
    chmod +x install.sh
    log "运行 install.sh（首次安装）..."
    ./install.sh
  fi

  # 标记初始化完成（在 install.sh 完成后写入）
  touch "$INITIALIZED_MARKER"
  log "初始化完成，已写入 $INITIALIZED_MARKER"
else
  log "检测到已初始化标记，跳过安装步骤（普通重启）"
fi

# ── 阶段 2：配置校验与自动修复 ───────────────────────────────
log "=== 配置校验阶段 ==="
if [ -f "$OC_CFG" ]; then
  # 读取 dmPolicy，通过参数传递路径避免插值风险
  DM_POLICY=$(python3 - "$OC_CFG" <<'PYEOF' 2>&1 || echo "__PYERR__"
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as f:
        d = json.load(f)
    val = d.get('channels', {}).get('feishu', {}).get('dmPolicy', '__MISSING__')
    print(val)
except Exception as e:
    print('__ERROR__:' + str(e))
PYEOF
)

  case "$DM_POLICY" in
    open|pairing|allowlist|__MISSING__)
      log "配置校验通过（channels.feishu.dmPolicy=${DM_POLICY}）"
      ;;
    __ERROR__*|__PYERR__*)
      warn "读取 $OC_CFG 失败：${DM_POLICY}"
      warn "尝试运行 openclaw doctor --fix ..."
      if ! openclaw doctor --fix 2>/dev/null; then
        warn "自动修复失败，请手动检查 $OC_CFG 后重启容器"
        exit 1
      fi
      ;;
    disabled|"")
      warn "⚠️ dmPolicy=\"${DM_POLICY:-<empty>}\"：私信功能已禁用或未配置"
      warn "飞书私信将被网关拒绝，所有 Agent 无法通过飞书通信"
      warn "建议修改 channels.feishu.dmPolicy 为 \"open\" 以启用私信功能"
      warn "自动修正为 \"open\"..."
      python3 - "$OC_CFG" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
try:
    d = json.loads(p.read_text(encoding='utf-8'))
    old_val = d.get('channels', {}).get('feishu', {}).get('dmPolicy', '__MISSING__')
    d.setdefault('channels', {}).setdefault('feishu', {})['dmPolicy'] = 'open'
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[entrypoint] 已修正 channels.feishu.dmPolicy: "{old_val}" -> "open"')
except Exception as e:
    print(f'[entrypoint][ERROR] 修正配置失败: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
      ;;
  esac
else
  warn "$OC_CFG 不存在，跳过配置校验"
fi

# ── 阶段 3：Gateway 启动 ──────────────────────────────────────
log "=== Gateway 启动阶段 ==="
log "starting openclaw gateway..."
openclaw gateway &
GATEWAY_PID=$!

log "waiting for gateway on 127.0.0.1:18789 ..."
GATEWAY_READY=false
for _ in $(seq 1 60); do
  if (echo > /dev/tcp/127.0.0.1/18789) >/dev/null 2>&1; then
    log "gateway is ready."
    GATEWAY_READY=true
    break
  fi
  # 检测 gateway 进程是否已意外退出
  if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
    warn "gateway 进程（PID=$GATEWAY_PID）已意外退出"
    break
  fi
  sleep 1
done

if [ "$GATEWAY_READY" = false ]; then
  warn "gateway 未在 60 秒内就绪，请检查配置："
  warn "  1. 运行 openclaw doctor --fix 修复配置"
  warn "  2. 检查 $OC_CFG 中的 channels.feishu.dmPolicy 是否为合法值"
  warn "  3. 修复后重启容器（不会重复执行安装步骤）"
  exit 1
fi

# 刷新循环后台
if [ -f /app/AgentClaw/scripts/run_loop.sh ]; then
  bash /app/AgentClaw/scripts/run_loop.sh &
fi

# 编排引擎（V8 核心：看板轮询 + Agent 派发）
log "starting pipeline orchestrator..."
python3 /app/AgentClaw/scripts/pipeline_orchestrator.py &
ORCH_PID=$!
log "pipeline orchestrator started (PID=$ORCH_PID)"

# 前台启动 dashboard（容器主进程）
exec python3 /app/AgentClaw/dashboard/server.py
