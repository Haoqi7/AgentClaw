#!/usr/bin/env bash
set -e

echo "[entrypoint] Initializing OpenClaw (first run may require manual config)..."

openclaw onboard --install-daemon || true
openclaw init || true

if [ -f /app/AgentClaw/install.sh ]; then
  cd /app/AgentClaw
  chmod +x install.sh || true
  ./install.sh || true
fi

openclaw gateway || true

if [ -f /app/AgentClaw/scripts/run_loop.sh ]; then
  bash /app/AgentClaw/scripts/run_loop.sh &
fi

exec python3 /app/AgentClaw/dashboard/server.py
