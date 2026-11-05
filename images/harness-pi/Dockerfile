# The pi harness profile image: the harness server and pi, one container per run.
#
#   docker build -t harness-pi:dev images/harness-pi
#
# The container holds no model provider key. pi's model calls go to the HTTP
# Gateway at GATEWAY_URL with the run's grant key. The task's repo, or the
# sample's empty workspace, is mounted at /workspace.

FROM node:22.23.3-bookworm-slim

# git: pi's tools run git in the task repo's clone. ripgrep and fd-find: pi's
# grep and find tools use rg and fd (or fdfind), and the per-run sandbox's
# egress allowlist does not let pi download them.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl ripgrep fd-find \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY server ./server
COPY extension ./extension

# Inside the container pi's tools reach only /workspace on the host, so the
# server may offer pi's bash tool to a profile that names it.
ENV HOST=0.0.0.0 \
    PORT=8090 \
    WORKSPACE_DIR=/workspace \
    SESSION_DIR=/sessions \
    HARNESS_ALLOW_BASH=1 \
    NODE_ENV=production

RUN mkdir -p /workspace /sessions && chmod 0777 /workspace /sessions
USER node
EXPOSE 8090
CMD ["node", "server/main.ts"]
