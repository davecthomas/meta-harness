#!/usr/bin/env bash
# Starts the services the sample needs and runs tasks on them. The Makefile calls it.
#
#   stack.sh run    start everything, run one task, stop everything (make run and the demos)
#   stack.sh up     start Temporal, Redis, the gateway and a worker, and leave them running
#   stack.sh task   run one task on the services `up` started, and wait for it
#   stack.sh down   stop what `up` started
#   stack.sh egress-check   check the per-run sandbox network rules, with no model call
#
# GATEWAY=fake starts run_task/fake_gateway.py, which makes no model calls,
# in place of ai-api-unified-http, and skips Redis. e2e.sh uses it.
#
# Every run gets its own per-run sandbox: a Docker container of the harness-pi
# image on its own internal network, which the run activity starts and removes
# (run_task/sandbox.py). Its only way out is the egress proxy (egress-proxy/),
# which this script starts. This script builds both images. The harness
# server's log and pi's workspace go in runs/<REF>/. Settings come from the
# environment, which the Makefile sets.

set -eo pipefail
cd "$(dirname "$0")"

STACK_DIR=stack
RUNS_DIR=${RUNS_DIR:-$PWD/runs}

temporal_pid=; redis_pid=; gateway_pid=; worker_pid=; tail_pid=; egress_started=
EGRESS_PROXY=mh-egress-proxy
EGRESS_NET=mh-egress-net
# The control listener lets the worker reach only the per-run sandboxes' harness server port.
CONTROL_ALLOW='mh-sandbox-*:8090'

log() { echo "[make] $*"; }

stop_group() {
  kill -TERM -- "-$1" 2>/dev/null || true
  wait "$1" 2>/dev/null || true
  log "$2 stopped"
}

wait_for() {
  local name=$1 check=$2 pid=$3 logfile=$4 second
  for second in $(seq 1 "$WAIT_SECONDS"); do
    "$check" && return 0
    kill -0 "$pid" 2>/dev/null || { log "$name exited during startup. See $logfile"; return 1; }
    sleep 1
  done
  log "$name did not answer within ${WAIT_SECONDS}s. See $logfile"
  return 1
}

check_docker() {
  command -v docker >/dev/null || { log "Docker is not installed. Install Docker Desktop."; exit 1; }
  docker info >/dev/null 2>&1 || { log "Docker is not running. Start Docker Desktop and try again."; exit 1; }
}

# The per-run sandbox image and the egress proxy image. Docker's layer cache makes a
# rebuild with no changes take a second or two.
build_image() {
  log "building the per-run sandbox image $HARNESS_IMAGE from $HARNESS_DIR"
  docker build --quiet --tag "$HARNESS_IMAGE" "$HARNESS_DIR" >/dev/null || {
    log "docker build failed. To see why, run: docker build -t $HARNESS_IMAGE $HARNESS_DIR"
    exit 1
  }
  docker build --quiet --tag "$EGRESS_IMAGE" egress-proxy >/dev/null || {
    log "docker build failed. To see why, run: docker build -t $EGRESS_IMAGE egress-proxy"
    exit 1
  }
}

# remove_sandboxes [REF]: removes per-run sandbox containers and networks this sample started,
# for one task or for all. The run activity removes each one when its run ends. This catches
# any a stopped run left behind.
remove_sandboxes() {
  local filter="label=meta-harness.ref=${1:-}" ids nets net
  [ -n "${1:-}" ] || filter="label=meta-harness.sample=1"
  ids=$(docker ps --all --quiet --filter "$filter" 2>/dev/null | grep -v "^$(egress_id)\$" || true)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086
    docker rm --force $ids >/dev/null 2>&1 || true
    log "removed leftover per-run sandbox containers: $(echo $ids)"
  fi
  nets=$(docker network ls --quiet --filter "$filter" --filter name=mh-run- 2>/dev/null || true)
  for net in $nets; do
    docker network disconnect --force "$net" "$EGRESS_PROXY" >/dev/null 2>&1 || true
    docker network rm "$net" >/dev/null 2>&1 || true
  done
  [ -z "$nets" ] || log "removed leftover per-run sandbox networks"
}

egress_id() { docker ps --all --quiet --filter "name=^${EGRESS_PROXY}\$" 2>/dev/null | head -n 1; }
egress_up() { [ -n "$(docker ps --quiet --filter "name=^${EGRESS_PROXY}\$" 2>/dev/null)" ]; }

# start_egress: uses the egress proxy already running, or starts it on its own ordinary network.
# The run activity attaches it to each run's internal network.
start_egress() {
  if egress_up; then
    log "using the egress proxy already running ($EGRESS_PROXY)"
    return 0
  fi
  docker rm --force "$EGRESS_PROXY" >/dev/null 2>&1 || true
  docker network inspect "$EGRESS_NET" >/dev/null 2>&1 \
    || docker network create --label meta-harness.sample=1 "$EGRESS_NET" >/dev/null
  log "starting the egress proxy. Per-run sandboxes may reach only: $EGRESS_ALLOW"
  docker run --detach --name "$EGRESS_PROXY" --network "$EGRESS_NET" \
    --label meta-harness.sample=1 --label meta-harness.role=egress-proxy \
    --add-host=host.docker.internal:host-gateway \
    --publish "127.0.0.1::8091" \
    --env EGRESS_ALLOW="$EGRESS_ALLOW" --env CONTROL_ALLOW="$CONTROL_ALLOW" \
    "$EGRESS_IMAGE" >/dev/null
  egress_started=1
  local second
  for second in $(seq 1 "$WAIT_SECONDS"); do
    docker logs "$EGRESS_PROXY" 2>&1 | grep -q 'control listener' && {
      log "egress proxy control listener on $(docker port "$EGRESS_PROXY" 8091/tcp | head -n 1)"
      return 0
    }
    egress_up || break
    sleep 1
  done
  log "the egress proxy did not start. See: docker logs $EGRESS_PROXY"
  exit 1
}

# stop_egress LOG: saves the egress proxy's log, then removes it and its network.
stop_egress() {
  docker inspect "$EGRESS_PROXY" >/dev/null 2>&1 || return 0
  docker logs "$EGRESS_PROXY" >"$1" 2>&1 || true
  docker rm --force "$EGRESS_PROXY" >/dev/null 2>&1 || true
  docker network rm "$EGRESS_NET" >/dev/null 2>&1 || true
  log "egress proxy stopped, log: $PWD/$1"
}

install() {
  check_docker
  "$UV" sync --quiet
  build_image
}

temporal_up() { temporal operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; }
redis_up() { [ "$(redis-cli -u "$REDIS_URL" ping 2>/dev/null)" = "PONG" ]; }
gateway_up() { curl -fsS "$GATEWAY_URL/healthz" >/dev/null 2>&1; }

# start_temporal LOG: uses a Temporal server already answering, or starts the dev server.
start_temporal() {
  if temporal_up; then
    log "using the Temporal server already running at $TEMPORAL_ADDRESS"
    return 0
  fi
  command -v temporal >/dev/null || { log "the Temporal CLI is not installed. Run: brew install temporal"; exit 1; }
  log "starting the Temporal dev server on port $TEMPORAL_PORT, log: $PWD/$1"
  local db_args=()
  [ -z "$TEMPORAL_DB" ] || db_args=(--db-filename "$TEMPORAL_DB")
  set -m
  nohup temporal server start-dev --port "$TEMPORAL_PORT" "${db_args[@]}" >"$1" 2>&1 </dev/null &
  temporal_pid=$!
  set +m
  wait_for "the Temporal dev server" temporal_up "$temporal_pid" "$1"
}

# start_redis LOG: uses a Redis already answering at REDIS_URL, or starts one on REDIS_PORT.
# The gateway keeps run grants in it. Nothing is saved to disk.
start_redis() {
  if redis_up; then
    log "using the Redis already running at $REDIS_URL"
    return 0
  fi
  command -v redis-server >/dev/null || { log "Redis is not installed. Run: brew install redis"; exit 1; }
  log "starting Redis on port $REDIS_PORT for the gateway's run grants, log: $PWD/$1"
  set -m
  nohup redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --save '' --appendonly no >"$1" 2>&1 </dev/null &
  redis_pid=$!
  set +m
  wait_for "Redis" redis_up "$redis_pid" "$1"
}

# A grant request for a run id nobody holds: 404 means grants are on, 503 means the gateway has no Redis.
grants_on() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $GATEWAY_API_KEY" \
    "$GATEWAY_URL/v1/run-grants/stack-sh-check")
  [ "$code" = 404 ]
}

# start_redis_if_needed LOG: the real gateway keeps run grants in Redis. The fake keeps them in memory.
start_redis_if_needed() {
  [ "$GATEWAY" = fake ] || start_redis "$1"
}

# start_fake_gateway LOG: the fake HTTP Gateway, which makes no model calls. A real gateway on the
# same port is refused, so a zero-cost run never spends money by accident.
start_fake_gateway() {
  if gateway_up; then
    curl -fsS "$GATEWAY_URL/healthz" | grep -q '"service_version": *"fake"' || {
      log "a real gateway answers at $GATEWAY_URL. The fake needs a free port: set GATEWAY_PORT."
      exit 1
    }
    log "using the fake gateway already running at $GATEWAY_URL"
    return 0
  fi
  log "starting the fake HTTP Gateway on $FAKE_GATEWAY_HOST:$GATEWAY_PORT, no model calls, log: $PWD/$1"
  set -m
  nohup .venv/bin/python -m run_task.fake_gateway --host "$FAKE_GATEWAY_HOST" --port "$GATEWAY_PORT" \
    >"$1" 2>&1 </dev/null &
  gateway_pid=$!
  set +m
  wait_for "the fake gateway" gateway_up "$gateway_pid" "$1"
}

# start_gateway LOG: uses a gateway already answering, or starts one from GATEWAY_DIR with run grants on.
# With GATEWAY=fake, starts the fake instead.
start_gateway() {
  if [ "$GATEWAY" = fake ]; then
    start_fake_gateway "$1"
    log "gateway: $(curl -fsS "$GATEWAY_URL/healthz")"
    return 0
  fi
  if gateway_up; then
    log "using the gateway already running at $GATEWAY_URL"
  else
    [ -d "$GATEWAY_DIR" ] || { log "no gateway checkout at $GATEWAY_DIR. Set GATEWAY_DIR=<path to ai-api-unified-http>"; exit 1; }
    log "starting the gateway from $GATEWAY_DIR with run grants in $REDIS_URL, log: $PWD/$1"
    set -m
    HTTP_REDIS_URL="$REDIS_URL" nohup make --no-print-directory -C "$GATEWAY_DIR" serve \
      PORT="$GATEWAY_PORT" DEV_API_KEY="$GATEWAY_API_KEY" >"$1" 2>&1 </dev/null &
    gateway_pid=$!
    set +m
    wait_for "the gateway" gateway_up "$gateway_pid" "$1"
  fi
  log "gateway: $(curl -fsS "$GATEWAY_URL/healthz")"
  grants_on || {
    log "the gateway at $GATEWAY_URL does not issue run grants. It needs ai-api-unified-http 1.18.0 or later,"
    log "started with HTTP_REDIS_URL set. Stop it and let this script start one."
    exit 1
  }
}

# start_worker LOG: starts a worker and waits until it polls the task queue.
start_worker() {
  local ready_count second
  ready_count=$(grep -c '^\[worker\] polling' "$1" 2>/dev/null || true)
  set -m
  # The route activity reads the router's tiers from ROUTER_CONFIG, with MODELS_ENV's entry applied.
  # The fake gateway's runs use the zero-cost entry, which turns the router's classifier off.
  local models_env=${MODELS_ENV:-}
  [ -n "$models_env" ] || [ "$GATEWAY" != fake ] || models_env=zero-cost
  AI_MODELS_CONFIG="$ROUTER_CONFIG" AI_MODELS_ENV="$models_env" \
    nohup .venv/bin/python -m run_task.worker >>"$1" 2>&1 </dev/null &
  worker_pid=$!
  set +m
  for second in $(seq 1 "$WAIT_SECONDS"); do
    [ "$(grep -c '^\[worker\] polling' "$1")" -gt "${ready_count:-0}" ] && return 0
    kill -0 "$worker_pid" 2>/dev/null || break
    sleep 1
  done
  log "the worker did not start. See $1"
  return 1
}

# check_ref: REF names the task's directory and its workflow id, so it must be plain and unused.
check_ref() {
  [ -n "$REF" ] || REF=$(uuidgen | cut -c1-8 | tr 'A-Z' 'a-z')
  if [[ ! "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    log "REF must be letters, digits, '.', '_' or '-', up to 64 characters, starting with a letter or digit"
    exit 1
  fi
  if [ -e "$RUNS_DIR/$REF" ]; then
    log "task $REF ran before, and runs/$REF holds its workspace. Use another REF."
    exit 1
  fi
}

starter() {
  .venv/bin/python -m run_task.starter --ref "$REF" --pin "$PIN" --floor "$FLOOR" --ceiling "$CEILING" \
    --spend-limit "$SPEND_LIMIT" --task "$TASK" "$@"
}

wait_for_log() {
  local tick
  for tick in $(seq 1 $((WAIT_SECONDS * 5))); do
    grep -qF -- "$1" worker.log && return 0
    sleep 0.2
  done
  log "worker.log never showed: $1"
  return 1
}

cmd_run() {
  if stack_alive worker; then
    log "make up is running, and its worker would take some of this run's work. Use make task, or run make down first."
    exit 1
  fi
  check_ref
  install
  stop_all() {
    [ -z "$worker_pid" ] || stop_group "$worker_pid" worker
    [ -z "$tail_pid" ] || { sleep 1; kill "$tail_pid" 2>/dev/null || true; wait "$tail_pid" 2>/dev/null || true; }
    remove_sandboxes "$REF"
    [ -z "$egress_started" ] || stop_egress egress-proxy.log
    [ -z "$gateway_pid" ] || stop_group "$gateway_pid" gateway
    [ -z "$redis_pid" ] || stop_group "$redis_pid" Redis
    [ -z "$temporal_pid" ] || stop_group "$temporal_pid" "Temporal dev server"
  }
  trap stop_all EXIT
  trap 'exit 130' INT TERM
  start_temporal temporal.log
  start_redis_if_needed redis.log
  start_gateway gateway.log
  start_egress
  : >worker.log
  tail -n +1 -F worker.log 2>/dev/null &
  tail_pid=$!
  start_worker worker.log
  local workflow_id="run-task-$REF" starter_pid status
  case "$DEMO" in
    cancel)
      starter &
      starter_pid=$!
      wait_for_log "$CANCEL_AT"
      log "temporal workflow cancel --workflow-id $workflow_id"
      temporal workflow cancel --address "$TEMPORAL_ADDRESS" --workflow-id "$workflow_id" >/dev/null
      status=0
      wait "$starter_pid" || status=$?
      [ "$status" -eq 3 ] || { log "expected the workflow to end canceled, starter exit $status"; exit 1; }
      ;;
    restart)
      starter &
      starter_pid=$!
      wait_for_log "$RESTART_AT"
      log "killing the worker with SIGKILL (process group $worker_pid). pi keeps running in the per-run sandbox."
      kill -KILL -- "-$worker_pid"
      wait "$worker_pid" 2>/dev/null || true
      log "Temporal retries run_harness once 10s pass with no heartbeat, plus the 2s retry interval"
      log "starting a new worker"
      start_worker worker.log
      wait "$starter_pid"
      ;;
    spend-limit)
      status=0
      starter || status=$?
      [ "$status" -eq 1 ] || { log "expected the task to fail at its spend limit, starter exit $status"; exit 1; }
      grep -qF "spend_limit" worker.log || { log "the task failed, but not at its spend limit. See worker.log"; exit 1; }
      log "the run stopped at its \$$SPEND_LIMIT spend limit, as expected"
      ;;
    fallback)
      starter
      grep -qF "escalates to" worker.log || { log "the task finished, but did not escalate a tier. See worker.log"; exit 1; }
      log "the first tier's candidates failed without a per-run sandbox, and the next tier finished the task"
      ;;
    *) starter ;;
  esac
}

# The stack file lists "name pid" for each service `up` started.
stack_pid() {
  [ -f "$STACK_DIR/pids" ] || return 0
  awk -v name="$1" '$1 == name { print $2 }' "$STACK_DIR/pids" | tail -n 1
}
stack_alive() { local pid; pid=$(stack_pid "$1"); [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; }

cmd_up() {
  if stack_alive worker; then
    log "the stack is already up. Logs are in $PWD/$STACK_DIR. Run: make down"
    return 0
  fi
  install
  mkdir -p "$STACK_DIR"
  # Keep the file: after a worker was killed, `up` starts only a new worker, and
  # `down` still needs the ids of the services an earlier `up` started.
  touch "$STACK_DIR/pids"
  # If startup fails partway, stop what this command started.
  up_failed() {
    [ -z "$worker_pid" ] || kill -TERM -- "-$worker_pid" 2>/dev/null || true
    cmd_down
  }
  trap '[ $? -eq 0 ] || up_failed' EXIT
  start_temporal "$STACK_DIR/temporal.log"
  [ -z "$temporal_pid" ] || echo "temporal $temporal_pid" >>"$STACK_DIR/pids"
  start_redis_if_needed "$STACK_DIR/redis.log"
  [ -z "$redis_pid" ] || echo "redis $redis_pid" >>"$STACK_DIR/pids"
  start_gateway "$STACK_DIR/gateway.log"
  [ -z "$gateway_pid" ] || echo "gateway $gateway_pid" >>"$STACK_DIR/pids"
  start_egress
  start_worker "$STACK_DIR/worker.log"
  echo "worker $worker_pid" >>"$STACK_DIR/pids"
  log "the stack is up. Worker log: $PWD/$STACK_DIR/worker.log"
  log "Temporal web UI: $TEMPORAL_UI_URL"
  log "next: make task TASK=\"...\" REF=<name>, then make down when you are done"
}

cmd_down() {
  local name pid second
  remove_sandboxes
  mkdir -p "$STACK_DIR"
  stop_egress "$STACK_DIR/egress-proxy.log"
  if [ ! -s "$STACK_DIR/pids" ]; then
    log "the stack is not up"
    return 0
  fi
  # Stop in reverse start order: worker, gateway, Redis, Temporal.
  for name in worker gateway redis temporal; do
    pid=$(stack_pid "$name")
    [ -n "$pid" ] || continue
    kill -TERM -- "-$pid" 2>/dev/null || true
    for second in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    log "$name stopped"
  done
  : >"$STACK_DIR/pids"
}

cmd_task() {
  if ! stack_alive worker; then
    log "the stack is not up. Run: make up"
    exit 1
  fi
  check_ref
  check_docker
  build_image
  start_egress
  trap 'remove_sandboxes "$REF"' EXIT
  trap 'exit 130' INT TERM
  local status=0
  starter --follow || status=$?
  # The starter exits 3 for a canceled task. A cancel is an outcome, not an error here.
  [ "$status" -eq 3 ] && return 0
  return "$status"
}

# The per-run sandbox network rules, checked from inside a throwaway per-run sandbox. No model call.
cmd_egress_check() {
  install
  stop_checked() {
    remove_sandboxes
    [ -z "$egress_started" ] || stop_egress egress-proxy.log
    [ -z "$gateway_pid" ] || stop_group "$gateway_pid" gateway
    [ -z "$redis_pid" ] || stop_group "$redis_pid" Redis
  }
  trap stop_checked EXIT
  trap 'exit 130' INT TERM
  start_redis_if_needed redis.log
  start_gateway gateway.log
  start_egress
  .venv/bin/python -m run_task.egress_check
}

case "${1:-}" in
  run) cmd_run ;;
  up) cmd_up ;;
  down) cmd_down ;;
  task) cmd_task ;;
  egress-check) cmd_egress_check ;;
  *) echo "usage: stack.sh run|up|down|task|egress-check" >&2; exit 2 ;;
esac
