FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    build-essential git curl ca-certificates bash \
    && python3 --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN npm install -g @qingchencloud/openclaw-zh@2026.3.24-zh.1

COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

RUN if [ -f requirements.txt ]; then python3 -m pip install --no-cache-dir -r requirements.txt; fi
RUN if [ -f package.json ]; then npm install; fi

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789
ENTRYPOINT ["/entrypoint.sh"]
