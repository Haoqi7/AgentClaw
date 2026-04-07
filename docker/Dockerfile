FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-pip python3.11-venv python3.11-dev \
    build-essential git curl ca-certificates bash \
    && python3.11 --version \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3

WORKDIR /app

RUN npm install -g @qingchencloud/openclaw-zh@2026.4.2-zh.1

COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

RUN if [ -f requirements.txt ]; then python3 -m pip install --no-cache-dir -r requirements.txt; fi

# Build frontend React app
RUN cd /app/AgentClaw/edict/frontend && npm install && npm run build

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789
ENTRYPOINT ["/entrypoint.sh"]
