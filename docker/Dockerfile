FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential git curl ca-certificates bash \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && python3 --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN npm install -g @qingchencloud/openclaw-zh@2026.4.14-zh.1


ARG TARGETARCH
COPY deps/ /tmp/deps/
RUN case "${TARGETARCH}" in \
      amd64) VEC_ARCH="linux-x86_64" ;; \
      arm64) VEC_ARCH="linux-aarch64" ;; \
      *)     echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    mkdir -p /root/.openclaw/extensions && \
    tar xf /tmp/deps/sqlite-vec-0.1.8-loadable-${VEC_ARCH}.tar.gz -C /tmp && \
    cp /tmp/vec0.so /root/.openclaw/extensions/ && \
    rm -rf /tmp/deps /tmp/vec0.so
    
COPY . /app/AgentClaw
WORKDIR /app/AgentClaw

# Build frontend React app
RUN cd /app/AgentClaw/frontend && npm install && npm run build


COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7891 18789 
ENTRYPOINT ["/entrypoint.sh"]
