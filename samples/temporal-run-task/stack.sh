#!/usr/bin/env bash
# Starts the services the sample needs and runs tasks on them. The Makefile calls it.
#
#   stack.sh run    start everything, run one task, stop everything (make run and the demos)
#   stack.sh up     start Temporal, the gateway and a worker, and leave them running
#   stack.sh task   run one task on the services `up` started, and wait for it
#   stack.sh down   stop what `up` started
#
# Every task gets its own harness server on a free port, because a harness
# server holds one session. Its log and pi's workspace go in runs/<REF>/.
# Settings come from the environment, which the Makefile sets.

set -eo pipefail
cd "$(dirname "$0")"

STACK_DIR=stack
RUNS_DIR=runs

temporal_pid=; gateway_pid=; harness_pid=; worker_pid=; tail_pid=
harness_url=; workspace=

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

load_node() {
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    log "nvm is not installed at $NVM_DIR. The harness server runs under the Node version in its .nvmrc."
    exit 1
  fi
  # shellcheck disable=SC1091
  source "$NVM_DIR/nvm.sh"
  (cd "$HARNESS_DIR" && nvm use >/dev/null) || {
    log "Node $(cat "$HARNESS_DIR/.nvmrc") is not installed. Run: nvm install $(cat "$HARNESS_DIR/.nvmrc")"
    exit 1
  }
}

install() {
  load_node
  "$UV" sync --quiet
  if [ ! -f "$HARNESS_DIR/node_modules/.package-lock.json" ] \
    || [ "$HARNESS_DIR/package-lock.json" -nt "$HARNESS_DIR/node_modules/.package-lock.json" ]; then
    (cd "$HARNESS_DIR" && nvm use >/dev/null && npm ci)
  fi
}

temporal_up() { temporal operator cluster health --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; }
gateway_up() { curl -fsS "$GATEWAY_URL/healthz" >/dev/null 2>&1; }
harness_up() { curl -fsS -H "Authorization: Bearer $HARNESS_SERVER_TOKEN" "$harness_url/health" >/dev/null 2>&1; }

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

# start_gateway LOG: uses a gateway already answering, or starts one from GATEWAY_DIR.
start_gateway() {
  if gateway_up; then
    log "using the gateway already running at $GATEWAY_URL"
  else
    [ -d "$GATEWAY_DIR" ] || { log "no gateway checkout at $GATEWAY_DIR. Set GATEWAY_DIR=<path to ai-api-unified-http>"; exit 1; }
    log "starting the gateway from $GATEWAY_DIR, log: $PWD/$1"
    set -m
    nohup make --no-print-directory -C "$GATEWAY_DIR" serve PORT="$GATEWAY_PORT" DEV_API_KEY="$GATEWAY_API_KEY" >"$1" 2>&1 </dev/null &
    gateway_pid=$!
    set +m
    wait_for "the gateway" gateway_up "$gateway_pid" "$1"
  fi
  log "gateway: $(curl -fsS "$GATEWAY_URL/healthz")"
}

# start_harness: a new harness server for task $REF on a free port. Sets harness_url and workspace.
start_harness() {
  local dir="$RUNS_DIR/$REF" logfile second
  workspace="$PWD/$dir/workspace"
  logfile="$dir/harness-server.log"
  mkdir -p "$workspace"
  log "starting a harness server for task $REF, log: $PWD/$logfile"
  set -m
  (cd "$HARNESS_DIR" && nvm use >/dev/null && PORT=0 WORKSPACE_DIR="$workspace" HEARTBEAT_SECONDS=2 exec node server/main.ts) \
    >"$logfile" 2>&1 </dev/null &
  harness_pid=$!
  set +m
  for second in $(seq 1 "$WAIT_SECONDS"); do
    harness_url=$(sed -n 's/.*listening on \(http:[^,]*\),.*/\1/p' "$logfile" | head -n 1)
    [ -n "$harness_url" ] && break
    kill -0 "$harness_pid" 2>/dev/null || { log "the harness server exited during startup. See $logfile"; exit 1; }
    sleep 1
  done
  [ -n "$harness_url" ] || { log "the harness server did not start within ${WAIT_SECONDS}s. See $logfile"; exit 1; }
  wait_for "the harness server" harness_up "$harness_pid" "$logfile"
  log "harness server at $harness_url: $(curl -fsS -H "Authorization: Bearer $HARNESS_SERVER_TOKEN" "$harness_url/health")"
  log "pi's workspace: $workspace"
}

# start_worker LOG: starts a worker and waits until it polls the task queue.
start_worker() {
  local ready_count second
  ready_count=$(grep -c '^\[worker\] polling' "$1" 2>/dev/null || true)
  set -m
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
  .venv/bin/python -m run_task.starter --ref "$REF" --harness-url "$harness_url" --workspace "$workspace" \
    --engine "$ENGINE" --model "$MODEL" --task "$TASK" "$@"
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
    [ -z "$harness_pid" ] || stop_group "$harness_pid" "harness server"
    [ -z "$gateway_pid" ] || stop_group "$gateway_pid" gateway
    [ -z "$temporal_pid" ] || stop_group "$temporal_pid" "Temporal dev server"
  }
  trap stop_all EXIT
  trap 'exit 130' INT TERM
  start_temporal temporal.log
  start_gateway gateway.log
  start_harness
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
      log "killing the worker with SIGKILL (process group $worker_pid). The harness server keeps running pi."
      kill -KILL -- "-$worker_pid"
      wait "$worker_pid" 2>/dev/null || true
      log "Temporal retries run_harness once 10s pass with no heartbeat, plus the 2s retry interval"
      log "starting a new worker"
      start_worker worker.log
      wait "$starter_pid"
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
  start_gateway "$STACK_DIR/gateway.log"
  [ -z "$gateway_pid" ] || echo "gateway $gateway_pid" >>"$STACK_DIR/pids"
  start_worker "$STACK_DIR/worker.log"
  echo "worker $worker_pid" >>"$STACK_DIR/pids"
  log "the stack is up. Worker log: $PWD/$STACK_DIR/worker.log"
  log "Temporal web UI: $TEMPORAL_UI_URL"
  log "next: make task TASK=\"...\" REF=<name>, then make down when you are done"
}

cmd_down() {
  local name pid second
  if [ ! -s "$STACK_DIR/pids" ]; then
    log "the stack is not up"
    return 0
  fi
  # Stop in reverse start order: worker, gateway, Temporal.
  for name in worker gateway temporal; do
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
  load_node
  stop_harness() { [ -z "$harness_pid" ] || stop_group "$harness_pid" "harness server for task $REF"; }
  trap stop_harness EXIT
  trap 'exit 130' INT TERM
  start_harness
  local status=0
  starter --follow || status=$?
  # The starter exits 3 for a canceled task. A cancel is an outcome, not an error here.
  [ "$status" -eq 3 ] && return 0
  return "$status"
}

case "${1:-}" in
  run) cmd_run ;;
  up) cmd_up ;;
  down) cmd_down ;;
  task) cmd_task ;;
  *) echo "usage: stack.sh run|up|down|task" >&2; exit 2 ;;
esac
