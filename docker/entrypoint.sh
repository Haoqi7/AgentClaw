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

  # ── 检测 OpenClaw 版本，决定使用非交互还是交互模式 ──
  # openclaw --version 是安全的，不会触发 @clack/prompts 交互向导
  # 新版（≥2026.4.20）的 onboard 交互向导使用 @clack/prompts raw mode，在 Docker 中会卡死
  # 旧版（≤2026.4.14）的 onboard 交互向导在 Docker 中正常工作
  _version_output=$(openclaw --version 2>&1 || true)
  OC_VERSION=$(echo "$_version_output" | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1 || true)

  USE_NON_INTERACTIVE=false
  if [ -n "$OC_VERSION" ]; then
    OC_VERSION_NUM=$(echo "$OC_VERSION" | awk -F. '{printf "%d%02d%02d\n", $1, $2, $3}')
    if [ "$OC_VERSION_NUM" -ge 20260420 ]; then
      USE_NON_INTERACTIVE=true
      log "OpenClaw 版本 $OC_VERSION ≥ 2026.4.20，使用非交互模式（解决 Docker 中 @clack/prompts 卡死）"
    else
      log "OpenClaw 版本 $OC_VERSION < 2026.4.20，使用交互模式"
    fi
  else
    warn "无法检测 OpenClaw 版本，默认使用非交互模式"
    USE_NON_INTERACTIVE=true
  fi

  # ── 运行 onboard ──
  if [ "$USE_NON_INTERACTIVE" = true ]; then
    log "运行 onboard --non-interactive..."
    timeout 60 openclaw onboard --non-interactive \
      --mode local \
      --auth-choice "${OPENCLAW_AUTH_CHOICE:-openai}" \
      --model "${OPENCLAW_MODEL:-gpt-4o}" \
      --install-daemon \
      || warn "onboard 超时或失败，已跳过"
  else
    log "运行 onboard（交互模式）..."
    timeout 60 openclaw onboard --install-daemon || warn "onboard 超时或失败，已跳过"
  fi

  # ── 运行 init ──
  if [ "$USE_NON_INTERACTIVE" = true ]; then
    log "运行 init --non-interactive..."
    timeout 60 openclaw init --non-interactive || warn "init 超时或失败，已跳过"
  else
    log "运行 init（默认模式）..."
    timeout 60 openclaw init || warn "init 超时或失败，已跳过"
  fi

  # 运行项目安装脚本（仅首次）
  if [ -f /app/AgentClaw/install.sh ]; then
    cd /app/AgentClaw
    chmod +x install.sh
    log "运行 install.sh（首次安装）..."
    ./install.sh || warn "install.sh 执行有错误，但继续启动..."
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
    *)
      warn "检测到无效配置：channels.feishu.dmPolicy=\"${DM_POLICY}\"（允许值：open, pairing, allowlist）"
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
GATEWAY_LOG="$OC_HOME/gateway-startup.log"
log "starting openclaw gateway（日志: $GATEWAY_LOG）..."
openclaw gateway > "$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!

log "waiting for gateway on 127.0.0.1:18789 (PID=$GATEWAY_PID) ..."
GATEWAY_READY=false
for _i in $(seq 1 90); do
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
  # 每 15 秒输出一次 gateway 日志，方便排查
  if [ $((_i % 15)) -eq 0 ] && [ -f "$GATEWAY_LOG" ]; then
    log "--- gateway 日志（等待中，已等 ${_i}s）---"
    tail -20 "$GATEWAY_LOG" 2>/dev/null || true
    log "--- 日志结束 ---"
  fi
  sleep 1
done

if [ "$GATEWAY_READY" = false ]; then
  warn "gateway 未在 90 秒内就绪"
  warn "=== gateway 启动日志 ==="
  if [ -f "$GATEWAY_LOG" ]; then
    cat "$GATEWAY_LOG"
  else
    warn "（无日志文件）"
  fi
  warn "=== 日志结束 ==="
  warn "请检查："
  warn "  1. openclaw.json 配置是否与当前版本兼容"
  warn "  2. API Key 是否已配置（运行 openclaw doctor 检查）"
  warn "  3. 运行 openclaw doctor --fix 尝试自动修复"
  warn "  4. 修复后重启容器（不会重复执行安装步骤）"
  exit 1
fi

# 刷新循环后台
if [ -f /app/AgentClaw/scripts/run_loop.sh ]; then
  bash /app/AgentClaw/scripts/run_loop.sh &
fi

# 前台启动 dashboard（容器主进程）
exec python3 /app/AgentClaw/dashboard/server.py
