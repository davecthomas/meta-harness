#!/bin/bash
# The VM's startup script, from its metadata (deploy/terraform/release). It runs at every boot, and
# again for every deploy that updates the running VM in place (MH_DEPLOY_MODE=update, started by the
# deploy workflow over IAP SSH): mount the data disk, get the meta-harness at the commit in the mh-ref
# metadata, and run its boot script. Output goes to /var/log/mh-boot.log, and the result to the guest
# attribute mh/boot, which the deploy waits for: "running", "ok <commit>" or "failed <commit>".
exec >>/var/log/mh-boot.log 2>&1
echo "[startup] $(date -u +%FT%TZ)"
MD=http://metadata.google.internal/computeMetadata/v1/instance
H="Metadata-Flavor: Google"
REF=$(curl -fs -H "$H" "$MD/attributes/mh-ref")
report() { curl -fs -X PUT -H "$H" --data "$1" "$MD/guest-attributes/mh/boot" >/dev/null || echo "[startup] could not report: $1"; }
report running

# The data disk (device name "data" in the release stack) holds Postgres, Temporal's history and run
# workspaces. A new disk has no file system and gets one; a disk from the last VM is mounted as it is.
DEV=/dev/disk/by-id/google-data
STATE=/var/lib/meta-harness
if ! blkid "$DEV" >/dev/null 2>&1; then
  mkfs.ext4 -q -F "$DEV"
fi
mkdir -p "$STATE"
grep -qs " $STATE " /proc/mounts || mount -o discard,defaults "$DEV" "$STATE"
grep -qs "$STATE" /etc/fstab || echo "$DEV $STATE ext4 discard,defaults,nofail 0 2" >>/etc/fstab

command -v git >/dev/null || { apt-get update -q && apt-get install -y -q git; }
# The checkout belongs to the mh user and this script runs as root, which git refuses without a
# safe.directory entry. A startup script has no HOME, so the entry goes in the system config.
git config --system --get-all safe.directory 2>/dev/null | grep -qx /opt/meta-harness ||
  git config --system --add safe.directory /opt/meta-harness
if [ ! -d /opt/meta-harness/.git ]; then
  git clone -q https://github.com/davecthomas/meta-harness.git /opt/meta-harness
fi
# The commit being replaced, so an update restarts only what changed since it.
export MH_PREVIOUS_COMMIT
MH_PREVIOUS_COMMIT=$(git -C /opt/meta-harness rev-parse HEAD 2>/dev/null || true)
if ! git -C /opt/meta-harness fetch -q origin "$REF"; then
  echo "[startup] could not fetch $REF"
  report "failed $REF"
  exit 1
fi
git -C /opt/meta-harness checkout -q -f FETCH_HEAD
if bash /opt/meta-harness/deploy/vm/boot.sh "$REF"; then
  report "ok $REF"
else
  echo "[startup] boot.sh failed"
  report "failed $REF"
  exit 1
fi
