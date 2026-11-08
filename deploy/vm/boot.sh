#!/usr/bin/env bash
# Brings the meta-harness VM to its running state. The VM's startup script (deploy/vm/startup.sh) runs
# it at every boot, and again for each deploy that updates the running VM in place. It is safe to run
# any number of times.
#
# At boot it starts everything. A deploy's update (MH_DEPLOY_MODE=update) keeps the VM serving: Postgres
# and Temporal keep running, the sandbox images are rebuilt only when their sources changed, the egress
# proxy and the HTTP Gateway restart only when they changed (a restart drops a running sandbox's model
# calls), and the control-plane services restart one at a time, each back up before the next. Running
# sandboxes are containers, so they keep going, and the run activity re-attaches to them when the
# worker comes back.
#
# The VM runs what a developer's machine runs: Postgres, the Temporal dev server (its history in a
# file on the disk), Redis for run grants, the HTTP Gateway, the event service, task service, planner
# and scheduler, the run worker, its per-run sandboxes and the egress proxy. Each is a systemd unit
# from deploy/vm/units, so it restarts after a crash or a reboot. Secrets come from Secret Manager into
# /etc/meta-harness/env, readable by root and the mh user only. Everything that must outlive the VM
# is under /var/lib/meta-harness, the data disk the startup script mounted.

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
mkdir -p "$STATE/runs" "$STATE/postgres" "$ETC"
chown mh:mh "$STATE" "$STATE/runs"

MODE=${MH_DEPLOY_MODE:-boot}
PREVIOUS=${MH_PREVIOUS_COMMIT:-}
GW_PREVIOUS=$(git -C "$GW" rev-parse HEAD 2>/dev/null || true)

log "code at $REF ($MODE)"
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
# changed PATH...: true when the meta-harness changed under PATH since the commit being replaced, or that
# commit is unknown.
changed() {
  [ -n "$PREVIOUS" ] || return 0
  ! git -C "$MH" diff --quiet "$PREVIOUS" HEAD -- "$@"
}
# healthy UNIT PORT: waits up to 60 seconds for the unit to be active and, with a port, to answer /health.
healthy() {
  for _ in $(seq 1 60); do
    if systemctl is-active -q "$1" && { [ -z "${2:-}" ] || curl -fs "http://127.0.0.1:$2/health" >/dev/null; }; then
      return 0
    fi
    sleep 1
  done
  log "$1 did not come back"
  return 1
}

if [ "$MODE" = boot ]; then
  # A boot starts everything.
  systemctl restart mh-postgres mh-temporal
  systemctl restart mh-images mh-egress mh-migrate
  systemctl restart mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker
else
  systemctl start mh-postgres mh-temporal
  systemctl restart mh-migrate
  if changed images samples/temporal-run-task/Makefile samples/temporal-run-task/egress-proxy deploy/vm/units/mh-images.service; then
    log "the sandbox images changed: rebuilding (running sandboxes keep their images)"
    systemctl restart mh-images
  fi
  if changed samples/temporal-run-task/egress-proxy deploy/vm/units/mh-egress.service; then
    systemctl restart mh-egress
  fi
  if [ "$(git -C "$GW" rev-parse HEAD)" != "$GW_PREVIOUS" ] || changed deploy/vm/units/mh-gateway.service; then
    systemctl restart mh-gateway
    healthy mh-gateway 8080
  fi
  # One at a time, each back before the next, so at most one service is down at once.
  for pair in mh-events:8301 mh-tasks:8302 mh-planner:8303 mh-scheduler:8304 mh-worker:; do
    unit=${pair%%:*}
    systemctl restart "$unit"
    healthy "$unit" "${pair#*:}"
  done
fi
log "done: $(systemctl is-active mh-postgres mh-temporal mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker | tr '\n' ' ')"
