#!/usr/bin/env bash
# Brings the meta-harness VM to its running state. The VM's startup script (deploy/vm/startup.sh) runs
# it at every boot, and again for each deploy that updates the running VM in place. It is safe to run
# any number of times.
#
# At boot it starts everything. A deploy's update (MH_DEPLOY_MODE=update) keeps the VM serving
# (update_services in services.sh): Postgres and Temporal keep running, and the services restart one at a
# time, each healthy before the next. Running sandboxes are containers, so they keep going, and the run
# activity re-attaches to them when the worker comes back. When any step of an update fails, rollback
# (services.sh) puts the previous commit back and restarts what the update restarted; the script then
# exits 3, or 4 when the rollback could not make every service healthy.
#
# The VM runs what a developer's machine runs: Postgres, the Temporal dev server (its history in a
# file on the disk), Redis for run grants, the HTTP Gateway, the event service, task service, planner
# and scheduler, the run worker, its per-run sandboxes and the egress proxy. Each is a systemd unit
# from deploy/vm/units, so it restarts after a crash or a reboot. Secrets come from Secret Manager into
# /etc/meta-harness/env, readable by root and the mh user only. Everything that must outlive the VM
# is under /var/lib/meta-harness, the data disk the startup script mounted.

# -E: functions inherit the ERR trap that rolls an update back.
set -eEuo pipefail
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

# The Ops Agent ships the journal, every mh-* unit's output, to Cloud Logging for the console's Logs page.
# Pinned to major version 2. Logs are a convenience: a failure here is logged and never stops a boot or
# an update. It runs near the end, once this commit is checked out.
ops_agent() {
  if ! systemctl list-unit-files google-cloud-ops-agent.service >/dev/null 2>&1; then
    curl -fsSo /tmp/add-ops-agent-repo.sh https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
    bash /tmp/add-ops-agent-repo.sh --also-install --version=2.*.*
  fi
  if ! cmp -s "$MH/deploy/vm/ops-agent.yaml" /etc/google-cloud-ops-agent/config.yaml; then
    install -m 0644 "$MH/deploy/vm/ops-agent.yaml" /etc/google-cloud-ops-agent/config.yaml
    systemctl restart google-cloud-ops-agent
  fi
}

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
# shellcheck source=deploy/vm/services.sh
source "$(dirname "${BASH_SOURCE[0]}")/services.sh"
# From here on, a failed update goes back to the commit it replaced. A boot has nothing to go back to.
[ "$MODE" = boot ] || trap rollback ERR

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
  # The most per-run sandboxes the VM holds: the ceiling on the task policy's max_parallel.
  echo "MH_MAX_SANDBOXES=8"
  # Every run gets a Postgres beside its sandbox, for its task's tests, unless its harness profile says otherwise.
  echo "MH_SANDBOX_SERVICES=postgres"
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
install_dependencies

log "systemd units"
install_units
systemctl enable mh-postgres mh-temporal mh-gateway mh-images mh-egress mh-migrate \
  mh-events mh-tasks mh-planner mh-scheduler mh-worker

if [ "$MODE" = boot ]; then
  # A boot starts everything.
  systemctl restart mh-postgres mh-temporal
  systemctl restart mh-images mh-egress mh-migrate
  systemctl restart mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker
  # A deploy that replaced the VM drained the scheduler first; this VM ends the drain once it is up.
  drained=
  for try in $(seq 1 30); do bash "$MH/deploy/vm/drain.sh" off && { drained=1; break; }; sleep 5; done
  [ -n "$drained" ] || log "could not end the drain: run deploy/vm/drain.sh off once the scheduler is up"
else
  update_services
fi
# After the code is checked out, so the config is this commit's. In a || list, a failure stops nothing.
ops_agent || log "ops agent: not installed or not configured; logs stay in the VM's journal"
log "done: $(systemctl is-active mh-postgres mh-temporal mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker | tr '\n' ' ')"
