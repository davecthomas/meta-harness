#!/usr/bin/env bash
# The deploy's smoke task, over IAP SSH from the deploy workflow once the VM runs the commit: one small task
# through the deployed system (src/meta_harness/smoke.py). Exits 1 when it does not end closed.
set -euo pipefail
set -a
# shellcheck source=/dev/null
source /etc/meta-harness/env
set +a
export MH_TASK_API_KEY=$MH_SERVICE_KEY
export MH_DEPLOYED_COMMIT=${1:-$(git -C /opt/meta-harness rev-parse HEAD)}
install -d -o mh -g mh /var/lib/meta-harness/smoke
cd /opt/meta-harness
sudo -u mh -E .venv/bin/mh deploy smoke
