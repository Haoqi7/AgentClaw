FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    build-essential git curl ca-certificates bash \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# OpenClaw CLI
RUN npm install -g @qingchencloud/openclaw-zh@latest

# Project
COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

# deps
RUN if [ -f requirements.txt ]; then python3 -m pip install --no-cache-dir -r requirements.txt; fi
RUN if [ -f package.json ]; then npm install; fi

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789
ENTRYPOINT ["/entrypoint.sh"]
