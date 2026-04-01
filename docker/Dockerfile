FROM node:22-bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    build-essential git curl ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# OpenClaw CLI
RUN npm install -g @qingchencloud/openclaw-zh@latest

# Project
COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

# deps
RUN if [ -f requirements.txt ]; then pip3 install -r requirements.txt; fi
RUN if [ -f package.json ]; then npm install; fi

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789
ENTRYPOINT ["/entrypoint.sh"]
