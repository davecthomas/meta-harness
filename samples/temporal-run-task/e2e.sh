#!/usr/bin/env bash
# The zero-cost end-to-end check. `make e2e` runs it, and CI runs it on every pull request.
#
# Every step runs the whole current path, the same as `make run` and the demos,
# against the fake HTTP Gateway (run_task/fake_gateway.py), which makes no model
# calls. It checks:
#
#   run           the task finishes, and pi wrote hello.txt with the fake's text into the workspace
#   cancel        a workflow cancel stops pi and the workflow ends canceled
#   restart       a worker killed mid-run: the next attempt re-attaches to the same per-run sandbox
#   spend-limit   the fake refuses turn 2 with 402 and the run ends on its spend limit
#   egress        the per-run sandbox network rules (stack.sh egress-check)
#   cleanup       no per-run sandbox container or network is left
#
# The Makefile sets GATEWAY=fake and the fake's port. Each step's output goes
# to e2e-logs/<step>.log. Exits 0 when every step passes.

set -uo pipefail
cd "$(dirname "$0")"

[ "${GATEWAY:-}" = fake ] || { echo "[e2e] run this through make e2e, which uses the fake gateway"; exit 2; }

LOGS=e2e-logs
STAMP=$(date +%Y%m%d%H%M%S)
mkdir -p "$LOGS"
failures=0

pass() { echo "[e2e] PASS $1"; }
fail() {
  echo "[e2e] FAIL $1. The last lines of $LOGS/$2.log:"
  tail -n 25 "$LOGS/$2.log" | sed 's/^/[e2e]   /'
  failures=$((failures + 1))
}

# step NAME: runs the rest of the line with its output in e2e-logs/NAME.log, and returns its exit code.
step() {
  local name=$1
  shift
  echo "[e2e] $name ..."
  "$@" >"$LOGS/$name.log" 2>&1
}

expected=$(.venv/bin/python -c 'from run_task.fake_gateway import EXPECTED_TEXT; print(EXPECTED_TEXT)' 2>/dev/null) \
  || { "$UV" sync --quiet && expected=$(.venv/bin/python -c 'from run_task.fake_gateway import EXPECTED_TEXT; print(EXPECTED_TEXT)'); }

ref="e2e-$STAMP-run"
if step run env REF="$ref" DEMO= ./stack.sh run \
  && grep -q 'status=FINISHED' "$LOGS/run.log" \
  && [ "$(cat "$RUNS_DIR/$ref/workspace/hello.txt" 2>/dev/null)" = "$expected" ]; then
  pass "run: the task finished and hello.txt holds \"$expected\""
else
  fail "run: expected status=FINISHED and hello.txt holding \"$expected\"" run
fi

if step cancel env REF="e2e-$STAMP-cancel" DEMO=cancel ./stack.sh run && grep -q 'run CANCELED' "$LOGS/cancel.log"; then
  pass "cancel: POST /cancel stopped pi and the workflow ended canceled"
else
  fail "cancel" cancel
fi

if step restart env REF="e2e-$STAMP-restart" DEMO=restart ./stack.sh run \
  && grep -q 'still running from an earlier attempt' "$LOGS/restart.log" \
  && grep -q 'reattached=True' "$LOGS/restart.log"; then
  pass "restart: attempt 2 found the same per-run sandbox and re-attached"
else
  fail "restart: expected attempt 2 to re-attach to the running per-run sandbox" restart
fi

if step spend-limit env REF="e2e-$STAMP-spend" DEMO=spend-limit SPEND_LIMIT=0.001 ./stack.sh run; then
  pass "spend-limit: the run stopped at its \$0.001 spend limit"
else
  fail "spend-limit" spend-limit
fi

if step egress ./stack.sh egress-check; then
  pass "egress: every per-run sandbox network rule held"
else
  fail "egress" egress
fi

containers=$(docker ps --all --quiet --filter label=meta-harness.sample=1 | wc -l | tr -d ' ')
networks=$(docker network ls --quiet --filter label=meta-harness.sample=1 | wc -l | tr -d ' ')
if [ "$containers" = 0 ] && [ "$networks" = 0 ]; then
  pass "cleanup: no per-run sandbox container or network left"
else
  echo "[e2e] FAIL cleanup: $containers containers and $networks networks left"
  docker ps --all --filter label=meta-harness.sample=1
  failures=$((failures + 1))
fi

if [ "$failures" -eq 0 ]; then
  echo "[e2e] all steps passed, with no model calls. Logs: $PWD/$LOGS"
  exit 0
fi
echo "[e2e] $failures step(s) failed. Logs: $PWD/$LOGS"
exit 1
