#!/usr/bin/env bash
# Brings the meta-harness VM to its running state. The VM's startup script runs it at every boot, and
# deploy/gcp.sh runs it again after a code change. It is safe to run any number of times.
#
# The VM runs what a developer's machine runs: Postgres, the Temporal dev server (its history in a
# file on the disk), Redis for run grants, the HTTP Gateway, the event service, task service, planner
# and scheduler, the run worker, its per-run sandboxes and the egress proxy. Each is a systemd unit
# from deploy/vm/units, so it restarts after a crash or a reboot. Secrets come from Secret Manager into
# /etc/meta-harness/env, readable by root and the mh user only.

set -euo pipefail
REF=${1:-$(curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/mh-ref" || echo main)}
PROJECT=$(curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/project/project-id")
HOME_DIR=/opt
MH=$HOME_DIR/meta-harness
GW=$HOME_DIR/ai-api-unified-http
STATE=/var/lib/meta-harness
ETC=/etc/meta-harness
log() { echo "[boot] $*"; }

log "packages"
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null || ! command -v redis-server >/dev/null; then
  apt-get update -q
  apt-get install -y -q git make curl jq ca-certificates python3 docker.io redis-server
fi
systemctl enable --now docker redis-server
if [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/0.12.5/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
if [ ! -x /usr/local/bin/temporal ]; then
  curl -sSf https://temporal.download/cli.sh | sh -s -- --version v1.10.0 --dir /usr/local/temporal
  ln -sf /usr/local/temporal/bin/temporal /usr/local/bin/temporal
fi

log "the mh user"
id mh >/dev/null 2>&1 || useradd --create-home --shell /bin/bash mh
usermod -aG docker mh

# Poetry, for the HTTP Gateway. Its Python goes in /opt, not root's home, so the mh user can run it.
if ! sudo -u mh /usr/local/bin/poetry --version >/dev/null 2>&1; then
  UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin \
    /usr/local/bin/uv tool install --force --python 3.12 poetry==2.1.4
  chmod -R a+rX /opt/uv-python /opt/uv-tools
fi
mkdir -p "$STATE/runs" "$ETC"
chown -R mh:mh "$STATE"

log "code at $REF"
# This script runs as root and the checkouts belong to mh, which git refuses without a safe.directory
# entry. It goes in the system config, because the startup script that runs this has no HOME.
for dir in "$MH" "$GW"; do
  git config --system --get-all safe.directory 2>/dev/null | grep -qx "$dir" || git config --system --add safe.directory "$dir"
done
for pair in "$MH https://github.com/davecthomas/meta-harness.git $REF" "$GW https://github.com/davecthomas/ai-api-unified-http.git main"; do
  read -r dir url ref <<<"$pair"
  if [ -d "$dir/.git" ]; then
    git -C "$dir" fetch -q origin "$ref"
    git -C "$dir" checkout -q -f FETCH_HEAD
  else
    git clone -q "$url" "$dir"
    git -C "$dir" fetch -q origin "$ref"
    git -C "$dir" checkout -q -f FETCH_HEAD
  fi
  chown -R mh:mh "$dir"
done

log "secrets from Secret Manager into $ETC/env"
secret() { gcloud secrets versions access latest --secret="$1" --project="$PROJECT"; }
umask 077
{
  pw=$(secret MH_POSTGRES_PASSWORD)
  service_key=$(secret MH_SERVICE_KEY)
  gateway_key=$(secret MH_GATEWAY_API_KEY)
  echo "MH_POSTGRES_PASSWORD=$pw"
  echo "MH_DATABASE_URL=postgresql://mh:$pw@127.0.0.1:5444/mh"
  echo "MH_SERVICE_KEYS=service:$service_key"
  echo "MH_SERVICE_KEY=$service_key"
  # Which GitHub App serves which repo owner is this deployment's data, from the MH_GITHUB_APPS secret:
  # owner=app_id pairs, comma-separated. Each owner's key is the secret MH_GITHUB_APP_KEY_<owner>.
  apps=
  for pair in $(secret MH_GITHUB_APPS | tr ',' ' '); do
    owner=${pair%%=*}
    secret "MH_GITHUB_APP_KEY_$owner" >"$ETC/github-app-$owner.pem.new"
    apps="$apps${apps:+,}$pair:$ETC/github-app-$owner.pem"
  done
  echo "MH_GITHUB_APPS=$apps"
  echo "MH_REPOS_CONFIG=$ETC/repos.json"
  echo "MH_MAX_RUNNING=2"
  echo "ANTHROPIC_API_KEY=$(secret ANTHROPIC_API_KEY)"
  echo "OPENAI_API_KEY=$(secret OPENAI_API_KEY)"
  echo "GOOGLE_GEMINI_API_KEY=$(secret GOOGLE_GEMINI_API_KEY)"
  echo "HTTP_API_KEYS=meta-harness:$gateway_key"
  echo "GATEWAY_API_KEY=$gateway_key"
  echo "GATEWAY_URL=http://127.0.0.1:8080"
  echo "HTTP_REDIS_URL=redis://127.0.0.1:6379/0"
  echo "TEMPORAL_ADDRESS=127.0.0.1:7233"
  # AI_MODELS_CONFIG is not shared. The worker's models.json holds the router's harness candidates, which
  # only the classify-only router can read; the HTTP Gateway reads its own config/models.json. Each unit
  # that needs the variable sets it (deploy/vm/units/mh-worker.service).
  echo "RUNS_DIR=$STATE/runs"
  # Router tiers, harness profiles and the repo registry are the versions operators edit in the console,
  # seeded from the files on the first read.
  echo "MH_CONFIG_SOURCE=db"
} >"$ETC/env.new"
secret MH_REPOS_CONFIG >"$ETC/repos.json.new"
mv "$ETC/env.new" "$ETC/env"
mv "$ETC/repos.json.new" "$ETC/repos.json"
for new in "$ETC"/github-app-*.pem.new; do mv "$new" "${new%.new}"; done
chown root:mh "$ETC"/env "$ETC"/repos.json "$ETC"/github-app-*.pem
chmod 640 "$ETC"/env "$ETC"/repos.json "$ETC"/github-app-*.pem
umask 022

log "dependencies"
sudo -u mh -H bash -c "cd $MH && /usr/local/bin/uv sync --frozen --no-dev -q"
sudo -u mh -H bash -c "cd $GW && /usr/local/bin/poetry config virtualenvs.in-project true && /usr/local/bin/poetry install --only main -q"

log "systemd units"
install -m 644 "$MH"/deploy/vm/units/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable mh-postgres mh-temporal mh-gateway mh-images mh-egress mh-migrate \
  mh-events mh-tasks mh-planner mh-scheduler mh-worker
# A code change restarts everything that runs code; Postgres and Temporal keep running.
systemctl restart mh-postgres mh-temporal
systemctl restart mh-images mh-egress mh-migrate
systemctl restart mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker
log "done: $(systemctl is-active mh-postgres mh-temporal mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker | tr '\n' ' ')"
