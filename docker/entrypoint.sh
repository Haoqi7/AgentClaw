#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] starting..."

# 仅首次初始化时执行；失败可继续（因为可能需要交互配置）
if [ ! -f /root/.openclaw/.initialized ]; then
  echo "[entrypoint] first-time openclaw onboard/init..."
  openclaw onboard --install-daemon || true
  openclaw init || true
  mkdir -p /root/.openclaw
  touch /root/.openclaw/.initialized
fi

# 安装项目（若存在）
if [ -f /app/AgentClaw/install.sh ]; then
  cd /app/AgentClaw
  chmod +x install.sh
  ./install.sh
fi

# 启动 gateway（关键服务，不吞错）
echo "[entrypoint] starting openclaw gateway..."
openclaw gateway &

# 等待 gateway 端口 18789 就绪
echo "[entrypoint] waiting for gateway on 127.0.0.1:18789 ..."
for i in $(seq 1 60); do
  if (echo > /dev/tcp/127.0.0.1/18789) >/dev/null 2>&1; then
    echo "[entrypoint] gateway is ready."
    break
  fi
  sleep 1
  if [ "$i" -eq 60 ]; then
    echo "[entrypoint] gateway not ready in 60s, exiting."
    exit 1
  fi
done

# 刷新循环后台
if [ -f /app/AgentClaw/scripts/run_loop.sh ]; then
  bash /app/AgentClaw/scripts/run_loop.sh &
fi

# 前台启动 dashboard（容器主进程）
exec python3 /app/AgentClaw/dashboard/server.py
