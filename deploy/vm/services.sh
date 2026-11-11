# Functions boot.sh uses to install the checked-out code and restart its services, and to roll an
# in-place update back to the commit it replaced. boot.sh sources this file; so does
# tests/test_deploy_rollback.py, with stand-ins for systemctl and curl.
#
# The caller sets MH and GW (the two checkouts), PREVIOUS and GW_PREVIOUS (the commits an update
# replaces) and defines log.

# The units an update restarted, in order, so a rollback restarts the same ones.
RESTARTED=()
HEALTH_TRIES=${HEALTH_TRIES:-60}

# The port whose /health a unit answers, or nothing for a unit with no HTTP server.
port_of() {
  case "$1" in
    mh-gateway) echo 8080 ;;
    mh-events) echo 8301 ;;
    mh-tasks) echo 8302 ;;
    mh-planner) echo 8303 ;;
    mh-scheduler) echo 8304 ;;
    *) echo "" ;;
  esac
}

# changed PATH...: true when the meta-harness changed under PATH since the commit being replaced, or that
# commit is unknown.
changed() {
  [ -n "$PREVIOUS" ] || return 0
  ! git -C "$MH" diff --quiet "$PREVIOUS" HEAD -- "$@"
}

# healthy UNIT PORT: waits up to HEALTH_TRIES seconds for the unit to be active and, with a port, to
# answer /health.
healthy() {
  for _ in $(seq 1 "$HEALTH_TRIES"); do
    if systemctl is-active -q "$1" && { [ -z "${2:-}" ] || curl -fs "http://127.0.0.1:$2/health" >/dev/null; }; then
      return 0
    fi
    sleep 1
  done
  log "$1 did not come back"
  return 1
}

# restart UNIT: restarts the unit and waits for it to be healthy. The unit is recorded first, so a rollback
# also restarts the one that failed.
restart() {
  RESTARTED+=("$1")
  systemctl restart "$1"
  healthy "$1" "$(port_of "$1")"
}

# The Python dependencies of both checkouts, for whatever commit each has checked out.
install_dependencies() {
  sudo -u mh -H bash -c "cd $MH && /usr/local/bin/uv sync --frozen --no-dev -q"
  sudo -u mh -H bash -c "cd $GW && /usr/local/bin/poetry config virtualenvs.in-project true && /usr/local/bin/poetry install --only main -q"
}

# The systemd units of the checked-out meta-harness.
install_units() {
  install -m 644 "$MH"/deploy/vm/units/*.service /etc/systemd/system/
  systemctl daemon-reload
}

# Applies this commit's schema migrations, as the mh-migrate unit does at boot, without touching the unit.
# Restarting mh-migrate would stop every service that Requires it (events, tasks, planner, scheduler) until
# it finished, all at once: the console answered nothing for as long as the slowest of them took to stop.
apply_migrations() {
  sudo -u mh -H bash -c "set -a; . /etc/meta-harness/env; set +a; cd $MH && .venv/bin/mh migrate"
}

# An in-place update. Postgres and Temporal keep running, the migrations are applied beside the running
# services (a migration only adds, so the old code runs against the new schema), the sandbox images are
# rebuilt only when their sources changed, the egress proxy and the HTTP Gateway restart only when they
# changed (a restart drops a running sandbox's model calls), and the control-plane services restart one at a
# time, each healthy before the next, so at most one is down at once.
update_services() {
  systemctl start mh-postgres mh-temporal
  apply_migrations
  if changed images samples/temporal-run-task/Makefile samples/temporal-run-task/egress-proxy deploy/vm/units/mh-images.service; then
    log "the sandbox images changed: rebuilding (running sandboxes keep their images)"
    restart mh-images
  fi
  if changed samples/temporal-run-task/egress-proxy deploy/vm/units/mh-egress.service; then
    restart mh-egress
  fi
  if [ "$(git -C "$GW" rev-parse HEAD)" != "$GW_PREVIOUS" ] || changed deploy/vm/units/mh-gateway.service; then
    restart mh-gateway
  fi
  local unit
  for unit in mh-events mh-tasks mh-planner mh-scheduler mh-worker; do
    restart "$unit"
  done
}

# The ERR trap of an in-place update: puts both checkouts back at the commits the update replaced,
# reinstalls their dependencies and units, and restarts every unit the update restarted, one at a time.
# Migrations only add (AGENTS.md), so the previous code runs against the new schema. Exits 3 when the
# previous commit is back and healthy, 4 when the rollback could not make it healthy.
rollback() {
  # A failure inside $(...) also fires the trap there. Only the main shell rolls back: the subshell
  # exits, and its failure fires the trap again in the main shell.
  [ "$BASH_SUBSHELL" -eq 0 ] || exit 1
  trap - ERR
  set +e
  if [ -z "$PREVIOUS" ]; then
    log "the update failed, and there is no previous commit to roll back to"
    exit 4
  fi
  log "the update failed: rolling back to $PREVIOUS"
  git -C "$MH" checkout -q -f "$PREVIOUS"
  if [ -n "$GW_PREVIOUS" ] && [ "$(git -C "$GW" rev-parse HEAD)" != "$GW_PREVIOUS" ]; then
    git -C "$GW" checkout -q -f "$GW_PREVIOUS"
  fi
  chown -R mh:mh "$MH" "$GW"
  install_dependencies
  install_units
  local unit healthy_all=true
  for unit in ${RESTARTED[@]+"${RESTARTED[@]}"}; do
    systemctl restart "$unit"
    healthy "$unit" "$(port_of "$unit")" || healthy_all=false
  done
  if [ "$healthy_all" = true ]; then
    log "rolled back to $PREVIOUS"
    exit 3
  fi
  log "the rollback to $PREVIOUS left a service down"
  exit 4
}
