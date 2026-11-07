# The pi harness profile image: the harness server and pi, one container per run.
#
#   docker build -t harness-pi:dev images/harness-pi
#
# The container holds no model provider key. pi's model calls go to the HTTP
# Gateway at GATEWAY_URL with the run's grant key. The task's repo is mounted
# at /workspace.

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM node:22.23.3-bookworm-slim

# git: pi's tools run git in the task repo's clone. ripgrep and fd-find: pi's
# grep and find tools use rg and fd (or fdfind), and the per-run sandbox's
# egress allowlist does not let pi download them. make and a C toolchain: a
# Python package with no wheel for this platform builds from source.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl ripgrep fd-find make build-essential \
 && rm -rf /var/lib/apt/lists/*

# Python 3.12, uv and Poetry, so a run can install and test the Python repos it works on
# (ai-api-unified and ai-api-unified-http use Poetry, meta-harness uses uv). Each is in
# /opt and on PATH for any user, because the sandbox runs as the host's uid, not node.
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv python install 3.12.11 \
 && ln -s "$(uv python find 3.12.11)" /usr/local/bin/python3.12 \
 && ln -s python3.12 /usr/local/bin/python3 \
 && ln -s python3 /usr/local/bin/python \
 && uv tool install --python 3.12.11 poetry==2.1.4 \
 && chmod -R a+rX /opt/python /opt/uv-tools \
 && python3 --version && poetry --version && uv --version

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY server ./server
COPY extension ./extension

# Inside the container pi's tools reach only /workspace on the host, so the
# server may offer pi's bash tool to a profile that names it.
#
# HOME, the caches and every virtualenv live under /tmp/home in the container,
# never in /workspace: a virtualenv in the workspace would land in the pull
# request, and the sandbox's uid has no home directory of its own.
ENV HOST=0.0.0.0 \
    PORT=8090 \
    WORKSPACE_DIR=/workspace \
    SESSION_DIR=/sessions \
    HARNESS_ALLOW_BASH=1 \
    NODE_ENV=production \
    HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/home/.cache \
    UV_CACHE_DIR=/tmp/home/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/tmp/home/venvs/uv \
    UV_PYTHON_DOWNLOADS=never \
    POETRY_CACHE_DIR=/tmp/home/.cache/poetry \
    POETRY_VIRTUALENVS_IN_PROJECT=false \
    POETRY_VIRTUALENVS_PATH=/tmp/home/venvs/poetry \
    PIP_CACHE_DIR=/tmp/home/.cache/pip

RUN mkdir -p /workspace /sessions /tmp/home && chmod 0777 /workspace /sessions /tmp/home
USER node
EXPOSE 8090
CMD ["node", "server/main.ts"]
