#!/usr/bin/env bash
# Takes the meta-harness out of its Google Cloud project, for a move. Run it from a developer's machine signed
# in to gcloud (an owner of the project) and to gh (an admin of the repo).
#
#   shutdown.sh status            what is running: the VM, the deploy workflow, the project's billing
#   shutdown.sh backup [DIR]      dumps the database (goals, tasks, events, config) to DIR
#                                 (default: MH_BACKUP_DIR, ~/meta-harness-backups)
#   shutdown.sh pause [--force]   stops it, keeping everything: turns the deploy workflow off so a merge cannot
#                                 start it again, drains (no new task starts), waits for running tasks to
#                                 finish, then stops the VM. --force stops the VM without waiting.
#   shutdown.sh resume            undoes pause: starts the VM, lets tasks start, turns the deploy workflow on
#   shutdown.sh disable           backs up, pauses, then unlinks the project's billing account: nothing more is billed.
#                                 Google may delete what the project holds if billing stays off
#   shutdown.sh delete            backs up, pauses (without waiting), then shuts the project down. Google keeps it 30 days, during which
#                                 `gcloud projects undelete meta-harness-prod` brings it back; then it is gone
#
# disable and delete ask once, before anything changes, for the project's id typed out. The make targets
# gcp-status, gcp-backup, gcp-pause, gcp-resume, gcp-disable and gcp-teardown run these.
set -Eeuo pipefail

# deploy/.env, when there is one, sets these without putting them in the repo (deploy/.env.example).
ENV_FILE="$(dirname "$0")/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

P=${MH_PROJECT:-meta-harness-prod}
Z=${MH_ZONE:-us-central1-a}
VM=mh-vm
REPO=${MH_REPO:-davecthomas/meta-harness}
WAIT_MINUTES=${MH_WAIT_MINUTES:-60}
BACKUPS=${MH_BACKUP_DIR:-$HOME/meta-harness-backups}

say() { echo "==> $*"; }
# Every step is safe to run again, so a failure says where things stand and to rerun.
trap 'echo "error: the step at line $LINENO failed (see above). Run $0 status to see where things stand, then run the same command again: each step is safe to repeat." >&2' ERR
die() { echo "error: $*" >&2; exit 1; }

vm() { gcloud compute ssh "$VM" --project "$P" --zone "$Z" --tunnel-through-iap --quiet --command "$1" </dev/null; }
vm_status() { gcloud compute instances describe "$VM" --project "$P" --zone "$Z" --format="value(status)" 2>/dev/null || echo MISSING; }

preflight() {
  command -v gcloud >/dev/null || die "gcloud is not installed. Install the Google Cloud CLI, then gcloud auth login."
  command -v gh >/dev/null || die "gh is not installed. Install the GitHub CLI, then gh auth login."
  gcloud projects describe "$P" --format="value(projectId)" >/dev/null 2>&1 ||
    die "cannot read project $P as $(gcloud config get-value account 2>/dev/null). Run gcloud auth login with an owner of $P, or set MH_PROJECT."
  gh auth status >/dev/null 2>&1 || die "gh is not signed in. Run gh auth login with an admin of $REPO."
}

confirm() {
  local typed
  echo "$1"
  read -r -p "Type the project id ($P) to go on: " typed
  [ "$typed" = "$P" ] || die "you typed '$typed', not '$P'. Nothing was changed by this step; run it again to retry."
}

status() {
  say "VM $VM: $(vm_status)"
  say "deploy workflow: $(gh api "repos/$REPO/actions/workflows/deploy.yml" -q .state 2>/dev/null || echo unknown)"
  say "billing: $(gcloud billing projects describe "$P" --format='value(billingEnabled,billingAccountName)' 2>/dev/null || echo unknown)"
  if [ "$(vm_status)" = RUNNING ]; then
    say "running tasks:"
    vm "sudo bash /opt/meta-harness/deploy/vm/drain.sh running" || true
  fi
}

backup() {
  local dir=${1:-$BACKUPS} file
  mkdir -p "$dir"
  [ "$(vm_status)" = RUNNING ] || die "the VM is $(vm_status), and the database is on it. Start it with: $0 resume (or gcloud compute instances start $VM --project $P --zone $Z), then back up."
  file="$dir/meta-harness-db-$(date -u +%Y%m%d-%H%M%S).sql.gz"
  say "dumping the database to $file"
  # The dump holds everything the database does: only this user may read it.
  umask 077
  # pipefail on the VM: a pg_dump that fails must fail the command, not leave gzip's valid, empty archive.
  vm "sudo bash -o pipefail -c 'docker exec mh-postgres pg_dump -U mh --clean --if-exists mh | gzip'" >"$file" ||
    die "the dump failed, so $file is not a backup (delete it). Check the database on the VM: sudo docker ps; sudo docker logs mh-postgres. Then run $0 backup again."
  [ -s "$file" ] || die "the dump is empty. Check the database on the VM: sudo docker ps; sudo docker logs mh-postgres."
  gzip -t "$file" || die "the dump in $file is damaged. Run $0 backup again."
  # pg_dump ends a whole dump with this line; without it the dump stopped part way.
  gunzip -c "$file" | tail -5 | grep -q "PostgreSQL database dump complete" ||
    die "the dump in $file stopped part way: it does not end with pg_dump's closing line. Delete it and run $0 backup again."
  say "saved $(du -h "$file" | cut -f1): restore with gunzip -c $file | psql <database url>"
  echo "Secret values (GitHub App keys, the repo registry) stay in Secret Manager; list them with:"
  echo "  gcloud secrets list --project $P"
}

pause() {
  local force=${1:-} running="" i
  say "turning the deploy workflow off, so a merge to main does not start the VM again"
  if [ "$(gh api "repos/$REPO/actions/workflows/deploy.yml" -q .state)" = active ]; then
    gh workflow disable deploy.yml --repo "$REPO"
  fi
  if [ "$(vm_status)" != RUNNING ]; then
    say "the VM is already $(vm_status)"
    return
  fi
  say "draining: no new task starts"
  vm "sudo bash /opt/meta-harness/deploy/vm/drain.sh on"
  if [ "$force" != --force ]; then
    for i in $(seq 1 "$WAIT_MINUTES"); do
      running=$(vm "sudo bash /opt/meta-harness/deploy/vm/drain.sh running")
      [ -z "$running" ] && break
      say "still running after $i of $WAIT_MINUTES minutes:"
      echo "$running"
      sleep 60
    done
    [ -z "$running" ] || die "tasks are still running after $WAIT_MINUTES minutes, so the VM was NOT stopped (it is drained: nothing new starts). Wait and run $0 pause again, set MH_WAIT_MINUTES, or stop them with $0 pause --force."
  fi
  say "stopping the VM"
  gcloud compute instances add-labels "$VM" --project "$P" --zone "$Z" --labels mh-paused=true
  gcloud compute instances stop "$VM" --project "$P" --zone "$Z"
  say "VM $VM: $(vm_status). The console shows errors until $0 resume."
}

resume() {
  say "starting the VM"
  gcloud compute instances start "$VM" --project "$P" --zone "$Z"
  gcloud compute instances remove-labels "$VM" --project "$P" --zone "$Z" --labels mh-paused || true
  say "waiting for the VM's services"
  local up=""
  for _ in $(seq 1 30); do
    if vm "sudo bash /opt/meta-harness/deploy/vm/drain.sh off" >/dev/null 2>&1; then
      up=yes
      break
    fi
    sleep 10
  done
  [ -n "$up" ] ||
    die "the VM started but its services did not answer. Read /var/log/mh-boot.log on it (gcloud compute ssh $VM --project $P --zone $Z --tunnel-through-iap), then run $0 resume again."
  gh workflow enable deploy.yml --repo "$REPO"
  say "running again; the deploy workflow is on"
}

# A backup before the project is changed: the database as it is now, or, when the VM is already stopped, the
# newest backup when it was made after the VM last stopped (the database cannot have changed since). Otherwise
# it starts the VM drained, so no task starts, and backs up.
backup_first() {
  local newest stopped made
  if [ "$(vm_status)" = RUNNING ]; then
    backup
    return
  fi
  newest=$(ls -t "$BACKUPS"/meta-harness-db-*.sql.gz 2>/dev/null | head -1 || true)
  if [ -n "$newest" ]; then
    stopped=$(gcloud compute instances describe "$VM" --project "$P" --zone "$Z" --format="value(lastStopTimestamp.date('%s'))")
    made=$(stat -f %m "$newest" 2>/dev/null || stat -c %Y "$newest")
    stopped=${stopped%%.*}
    if [ -n "$stopped" ] && [ "$made" -gt "$stopped" ]; then
      say "the VM has been stopped since before the newest backup was made, so that backup is current: $newest"
      return
    fi
  fi
  say "the VM is stopped and no backup in $BACKUPS is newer than its last stop: starting it, drained, to back up"
  gcloud compute instances start "$VM" --project "$P" --zone "$Z"
  for _ in $(seq 1 30); do
    vm "sudo bash /opt/meta-harness/deploy/vm/drain.sh on" >/dev/null 2>&1 && break
    sleep 10
  done
  backup
}

disable() {
  local account
  account=$(gcloud billing projects describe "$P" --format="value(billingAccountName)")
  confirm "This backs up the database to $BACKUPS, stops the meta-harness once running tasks finish, and unlinks billing account ${account:-none} from $P. Nothing more is billed, and Google may delete what the project holds if billing stays off."
  backup_first
  pause "${1:-}"
  say "unlinking billing"
  gcloud billing projects unlink "$P"
  say "billing is off. To turn it back on: gcloud billing projects link $P --billing-account ${account#billingAccounts/}, then $0 resume"
}

delete() {
  confirm "This backs up the database to $BACKUPS, stops the meta-harness without waiting for running tasks, and shuts down project $P: the VM, its disks, the database, secrets, the console's hosting and the Terraform state. Google keeps the project 30 days, during which gcloud projects undelete $P brings it back; then it is gone."
  backup_first
  pause --force
  say "shutting down project $P"
  gcloud projects delete "$P" --quiet
  say "done. To undo within 30 days: gcloud projects undelete $P. The deploy workflow is off; remove it or point it at the new project."
}

case "${1:-}" in
  status | backup | pause | resume | disable | delete)
    cmd=$1
    shift
    preflight
    "$cmd" "$@"
    ;;
  *)
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
