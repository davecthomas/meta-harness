#!/bin/bash
# The VM's startup script (instance metadata): get the meta-harness at the ref in the mh-ref metadata,
# then run its boot script. Output goes to /var/log/mh-boot.log.
exec >>/var/log/mh-boot.log 2>&1
echo "[startup] $(date -u +%FT%TZ)"
REF=$(curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/mh-ref" || echo main)
command -v git >/dev/null || { apt-get update -q && apt-get install -y -q git; }
# The checkout belongs to the mh user and this script runs as root, which git refuses without a
# safe.directory entry. A startup script has no HOME, so the entry goes in the system config.
git config --system --get-all safe.directory 2>/dev/null | grep -qx /opt/meta-harness ||
  git config --system --add safe.directory /opt/meta-harness
if [ ! -d /opt/meta-harness/.git ]; then
  git clone -q https://github.com/davecthomas/meta-harness.git /opt/meta-harness
fi
# A ref that no longer exists, such as a merged and deleted branch, stops here rather than booting old code.
if ! git -C /opt/meta-harness fetch -q origin "$REF"; then
  echo "[startup] could not fetch $REF. Set the mh-ref metadata to a branch, tag or commit that exists: deploy/gcp.sh update REF=main"
  exit 1
fi
git -C /opt/meta-harness checkout -q -f FETCH_HEAD
bash /opt/meta-harness/deploy/vm/boot.sh "$REF"
