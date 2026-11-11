# The edge on Cloud Run: the console web app and the /v1 routing, behind Identity-Aware Proxy.
#
#   gcloud builds submit --config deploy/cloudbuild-edge.yaml .

FROM node:22.23.3-bookworm-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM python:3.12.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
# git: uv installs ai-api-unified from its git repo.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev
COPY --from=console /console/dist ./console/dist
# The commit's version, which the deploy works out from main's history: the image holds no history to read.
ARG MH_VERSION=
ENV PATH=/app/.venv/bin:$PATH \
    MH_VERSION=$MH_VERSION
CMD ["sh", "-c", "exec mh edge serve --host 0.0.0.0 --port ${PORT:-8080}"]
