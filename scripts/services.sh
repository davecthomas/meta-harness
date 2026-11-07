#!/usr/bin/env bash
# Start or stop the control-plane services on this machine, each in the background
# with its log and pid in stack/. `make serve` and `make stop` call this.
#
#   scripts/services.sh start   start every service not already running, then wait for /health
#   scripts/services.sh stop    stop them
#   scripts/services.sh status  which are running

set -euo pipefail
cd "$(dirname "$0")/.."
STACK_DIR=stack
mkdir -p "$STACK_DIR"

# name, port, command
SERVICES=(
  "events 8301 uv run mh events serve --port 8301"
  "tasks 8302 uv run mh tasks serve --port 8302"
  "planner 8303 uv run mh planner serve --port 8303"
  "scheduler 8304 uv run mh scheduler serve --port 8304"
  "edge 8300 uv run mh edge serve --port 8300"
)

running() { [ -f "$STACK_DIR/$1.pid" ] && kill -0 "$(cat "$STACK_DIR/$1.pid")" 2>/dev/null; }

start() {
  for entry in "${SERVICES[@]}"; do
    read -r name port cmd <<<"$entry"
    if running "$name"; then
      echo "serve: $name already running"
      continue
    fi
    # shellcheck disable=SC2086
    nohup $cmd >"$STACK_DIR/$name.log" 2>&1 &
    echo $! >"$STACK_DIR/$name.pid"
    for _ in $(seq 1 60); do
      if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then break; fi
      if ! running "$name"; then echo "serve: $name exited; see $STACK_DIR/$name.log" >&2; exit 1; fi
      sleep 0.5
    done
    curl -fsS "http://127.0.0.1:$port/health" >/dev/null || { echo "serve: $name not healthy; see $STACK_DIR/$name.log" >&2; exit 1; }
    echo "serve: $name on http://127.0.0.1:$port, log in $STACK_DIR/$name.log"
  done
}

stop() {
  for entry in "${SERVICES[@]}"; do
    read -r name _ <<<"$entry"
    if running "$name"; then
      pkill -P "$(cat "$STACK_DIR/$name.pid")" 2>/dev/null || true
      kill "$(cat "$STACK_DIR/$name.pid")" 2>/dev/null || true
      echo "stop: $name"
    fi
    rm -f "$STACK_DIR/$name.pid"
  done
}

status() {
  for entry in "${SERVICES[@]}"; do
    read -r name port _ <<<"$entry"
    if running "$name"; then echo "$name: running on $port"; else echo "$name: stopped"; fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
