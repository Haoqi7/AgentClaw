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

  # ── 检测 OpenClaw 版本 ──
  # openclaw --version 是安全的，不会触发 @clack/prompts 交互向导
  _version_output=$(openclaw --version 2>&1 || true)
  OC_VERSION=$(echo "$_version_output" | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1 || true)

  # ── 决定 onboard 运行方式 ──
  # 新版（≥2026.4.20）的交互向导使用 @clack/prompts raw mode，在 Docker 中会卡死
  # 解决方案：使用 script -qc 提供真正的伪终端（PTY），让 raw mode 正常工作
  # 旧版（≤2026.4.14）的交互向导在 Docker 中正常工作，不需要特殊处理
  USE_SCRIPT_WRAPPER=false
  if [ -n "$OC_VERSION" ]; then
    OC_VERSION_NUM=$(echo "$OC_VERSION" | awk -F. '{printf "%d%02d%02d\n", $1, $2, $3}')
    if [ "$OC_VERSION_NUM" -ge 20260420 ]; then
      USE_SCRIPT_WRAPPER=true
      log "OpenClaw 版本 $OC_VERSION ≥ 2026.4.20，使用 script -qc 包装（解决 @clack/prompts Docker 卡死）"
    else
      log "OpenClaw 版本 $OC_VERSION < 2026.4.20，使用标准交互模式"
    fi
  else
    warn "无法检测 OpenClaw 版本，默认使用 script -qc 包装"
    USE_SCRIPT_WRAPPER=true
  fi

  # ── 运行 onboard ──
  if [ "$USE_SCRIPT_WRAPPER" = true ]; then
    log "运行 onboard（script -qc 包装）..."
    timeout 120 script -qc "openclaw onboard --install-daemon" /dev/null \
      || warn "onboard 超时或失败，已跳过"
  else
    log "运行 onboard（标准交互模式）..."
    timeout 60 openclaw onboard --install-daemon || warn "onboard 超时或失败，已跳过"
  fi

  # ── 运行 init ──
  log "运行 init..."
  if [ "$USE_SCRIPT_WRAPPER" = true ]; then
    timeout 120 script -qc "openclaw init" /dev/null \
      || warn "init 超时或失败，已跳过"
  else
    timeout 60 openclaw init || warn "init 超时或失败，已跳过"
  fi

  # ── 确保 openclaw.json 中包含 gateway.mode=local ──
  # 新版 gateway 启动必须此字段，否则报错：
  # "Missing config. Run `openclaw setup` or set gateway.mode=local"
  if [ -f "$OC_CFG" ]; then
    log "检查 gateway.mode 配置..."
    python3 - "$OC_CFG" <<'PYEOF'
import json, sys
try:
    p = sys.argv[1]
    with open(p, 'r', encoding='utf-8') as f:
        d = json.load(f)
    gw = d.setdefault('gateway', {})
    if gw.get('mode') != 'local':
        gw['mode'] = 'local'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print('[entrypoint] 已设置 gateway.mode=local')
    else:
        print('[entrypoint] gateway.mode=local 已存在')
except Exception as e:
    print(f'[entrypoint][WARN] 设置 gateway.mode 失败: {e}', file=sys.stderr)
PYEOF
  fi

  # ── 仅在 openclaw.json 存在时才运行 install.sh 和写入初始化标记 ──
  if [ ! -f "$OC_CFG" ]; then
    warn "初始化失败：openclaw.json 不存在"
    warn "请手动进入容器运行: script -qc 'openclaw onboard --install-daemon' /dev/null"
    warn "完成后重启容器"
    # 不 exit 1，改为启动 gateway --allow-unconfigured 让用户通过 WebUI 配置
    log "尝试以 --allow-unconfigured 模式启动 gateway..."
    OPENCLAW_ALLOW_UNCONFIGURED=true
  else
    OPENCLAW_ALLOW_UNCONFIGURED=false
  fi

  # 运行项目安装脚本（仅首次）
  if [ -f /app/AgentClaw/install.sh ] && [ "$OPENCLAW_ALLOW_UNCONFIGURED" = false ]; then
    cd /app/AgentClaw
    chmod +x install.sh
    log "运行 install.sh（首次安装）..."
    ./install.sh || warn "install.sh 执行有错误，但继续启动..."
  fi

  # 标记初始化完成（仅在 openclaw.json 存在时写入）
  if [ "$OPENCLAW_ALLOW_UNCONFIGURED" = false ]; then
    touch "$INITIALIZED_MARKER"
    log "初始化完成，已写入 $INITIALIZED_MARKER"
  fi
else
  log "检测到已初始化标记，跳过安装步骤（普通重启）"
  OPENCLAW_ALLOW_UNCONFIGURED=false
fi

# ── 阶段 2：配置校验与自动修复 ───────────────────────────────
log "=== 配置校验阶段 ==="
if [ -f "$OC_CFG" ]; then
  # 确保 gateway.mode=local（覆盖升级场景：旧配置可能缺少此字段）
  _gw_mode=$(python3 - "$OC_CFG" <<'PYEOF' 2>&1 || echo "__PYERR__"
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as f:
        d = json.load(f)
    val = d.get('gateway', {}).get('mode', '__MISSING__')
    print(val)
except Exception as e:
    print('__ERROR__:' + str(e))
PYEOF
)

  case "$_gw_mode" in
    local)
      log "gateway.mode=local 已确认"
      ;;
    __MISSING__)
      warn "gateway.mode 缺失，自动设置 gateway.mode=local..."
      python3 - "$OC_CFG" <<'PYEOF'
import json, sys
p = sys.argv[1]
with open(p, 'r', encoding='utf-8') as f:
    d = json.load(f)
d.setdefault('gateway', {})['mode'] = 'local'
with open(p, 'w', encoding='utf-8') as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print('[entrypoint] 已补充 gateway.mode=local')
PYEOF
      ;;
    __ERROR__*|__PYERR__*)
      warn "读取 openclaw.json gateway.mode 失败：${_gw_mode}"
      ;;
    *)
      log "gateway.mode=${_gw_mode}（非 local 模式）"
      ;;
  esac

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

# 读取 gateway.bind 配置，决定检测哪个地址
GW_BIND="loopback"
GW_CHECK_HOST="127.0.0.1"
if [ -f "$OC_CFG" ]; then
  GW_BIND=$(python3 - "$OC_CFG" <<'PYEOF' 2>&1 || echo "loopback"
import json, sys
try:
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        d = json.load(f)
    print(d.get('gateway', {}).get('bind', 'loopback'))
except Exception:
    print('loopback')
PYEOF
)
  if [ "$GW_BIND" = "lan" ]; then
    GW_CHECK_HOST="0.0.0.0"
    log "gateway.bind=lan，将检测 0.0.0.0:18789"
  fi
fi

if [ "$OPENCLAW_ALLOW_UNCONFIGURED" = true ]; then
  log "starting openclaw gateway --allow-unconfigured（日志: $GATEWAY_LOG）..."
  openclaw gateway --allow-unconfigured > "$GATEWAY_LOG" 2>&1 &
else
  log "starting openclaw gateway（日志: $GATEWAY_LOG）..."
  openclaw gateway > "$GATEWAY_LOG" 2>&1 &
fi
GATEWAY_PID=$!

log "waiting for gateway on ${GW_CHECK_HOST}:18789 (PID=$GATEWAY_PID) ..."
GATEWAY_READY=false
for _i in $(seq 1 90); do
  # 主检测地址
  if (echo > /dev/tcp/${GW_CHECK_HOST}/18789) >/dev/null 2>&1; then
    log "gateway is ready."
    GATEWAY_READY=true
    break
  fi
  # lan 模式回退检测 127.0.0.1
  if [ "$GW_BIND" = "lan" ]; then
    if (echo > /dev/tcp/127.0.0.1/18789) >/dev/null 2>&1; then
      log "gateway is ready (detected on 127.0.0.1)."
      GATEWAY_READY=true
      break
    fi
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
  warn "  1. openclaw.json 中 gateway.mode 是否为 local"
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
