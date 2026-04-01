FROM node:22-bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install OpenClaw CLI (per README requirement)
RUN npm install -g @qingchencloud/openclaw-zh@latest

# Copy project
COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

# Python deps (if provided)
RUN if [ -f requirements.txt ]; then pip3 install -r requirements.txt; fi

# Frontend deps (if provided)
RUN if [ -f package.json ]; then npm install; fi

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891
ENTRYPOINT ["/entrypoint.sh"]