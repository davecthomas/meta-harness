#!/bin/bash
# The VM's deployer: deploys without SSH. deploy/gcp.sh vm (and CI on every merge to main) sets two
# metadata values, mh-ref (the commit to run) and mh-deploy-id (a new id per deploy). This service
# waits for mh-deploy-id to change and runs the deploy in its own transient unit, so the boot script
# may restart this service without stopping the deploy. Each deploy's result goes to the guest attribute
# mh/deploy as "<id> running", "<id> ok <commit> ..." or "<id> failed <reason>"; gcp.sh waits on it.
#
#   mh-deployer watch     the service's loop (mh-deployer.service)
#   mh-deployer run ID    one deploy: the latest startup script from metadata, which runs boot.sh
set -u
MD=http://metadata.google.internal/computeMetadata/v1/instance
H="Metadata-Flavor: Google"
DONE=/var/lib/meta-harness/deployed-id
UNITS="mh-postgres mh-temporal mh-gateway mh-events mh-tasks mh-planner mh-scheduler mh-worker"

report() { curl -fs -X PUT -H "$H" --data "$2" "$MD/guest-attributes/mh/$1" >/dev/null || echo "[deployer] could not write guest attribute mh/$1"; }

run() {
  local id=$1 active
  echo "$id" >"$DONE"
  report deploy "$id running"
  if ! curl -fs -H "$H" "$MD/attributes/startup-script" >/run/mh-startup.sh; then
    report deploy "$id failed: the startup script is not in the VM's metadata"
    return 1
  fi
  if ! bash /run/mh-startup.sh; then
    report deploy "$id failed: the boot script exited nonzero; see /var/log/mh-boot.log"
    return 1
  fi
  active=$(systemctl is-active $UNITS | paste -sd' ')
  if [ "$active" != "$(echo $UNITS | sed 's/[^ ]*/active/g')" ]; then
    report deploy "$id failed: services are $active"
    return 1
  fi
  report deploy "$id ok $(git -C /opt/meta-harness rev-parse --short HEAD)"
}

watch() {
  local etag=NONE headers id
  headers=$(mktemp)
  report deployer ready
  while true; do
    if ! id=$(curl -fs -H "$H" -D "$headers" \
      "$MD/attributes/mh-deploy-id?wait_for_change=true&timeout_sec=600&last_etag=$etag"); then
      sleep 10 # no deploy has been requested yet, or the metadata server is busy
      continue
    fi
    etag=$(awk 'tolower($1)=="etag:" {print $2}' "$headers" | tr -d '\r')
    if [ -n "$id" ] && [ "$id" != "$(cat "$DONE" 2>/dev/null)" ]; then
      echo "[deployer] deploy $id"
      systemd-run --unit "mh-deploy-$id" --collect --setenv=HOME=/root /usr/local/bin/mh-deployer run "$id" ||
        echo "[deployer] deploy $id is already running"
    fi
  done
}

case "${1:-}" in
  watch) watch ;;
  run) run "$2" ;;
  *) echo "usage: mh-deployer watch|run ID" >&2; exit 2 ;;
esac
