FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential git curl ca-certificates bash \
    && python3 --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN npm install -g @qingchencloud/openclaw-zh@2026.4.2-zh.1

COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

RUN if [ -f edict/backend/requirements.txt ]; then python3 -m pip install --no-cache-dir --break-system-packages -r edict/backend/requirements.txt; fi

# Build frontend React app
# RUN cd /app/AgentClaw/edict/frontend && npm install && npm run build
RUN cd /app/AgentClaw/edict/frontend && npm ci

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789
ENTRYPOINT ["/entrypoint.sh"]
