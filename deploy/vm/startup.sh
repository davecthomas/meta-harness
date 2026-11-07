#!/bin/bash
# The VM's startup script (instance metadata): get the meta-harness at the ref in the mh-ref metadata,
# then run its boot script. Output goes to /var/log/mh-boot.log.
exec >>/var/log/mh-boot.log 2>&1
echo "[startup] $(date -u +%FT%TZ)"
REF=$(curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/mh-ref" || echo main)
command -v git >/dev/null || { apt-get update -q && apt-get install -y -q git; }
git config --global --add safe.directory /opt/meta-harness >/dev/null 2>&1 || true
if [ ! -d /opt/meta-harness/.git ]; then
  git clone -q https://github.com/davecthomas/meta-harness.git /opt/meta-harness
fi
git -C /opt/meta-harness fetch -q origin "$REF"
git -C /opt/meta-harness checkout -q -f FETCH_HEAD
bash /opt/meta-harness/deploy/vm/boot.sh "$REF"
