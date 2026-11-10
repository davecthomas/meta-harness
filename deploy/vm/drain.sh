#!/usr/bin/env bash
# Drains the meta-harness before a deploy replaces the VM, over IAP SSH from the deploy workflow:
#
#   drain.sh on        no new task starts; running tasks finish
#   drain.sh off       tasks start again (the new VM's boot runs this)
#   drain.sh running   the running tasks, one per line; nothing when none is
set -euo pipefail
set -a
# shellcheck source=/dev/null
source /etc/meta-harness/env
set +a
export MH_TASK_API_KEY=$MH_SERVICE_KEY
cd /opt/meta-harness
case "${1:-}" in
  on | off) sudo -u mh -E .venv/bin/mh scheduler drain "$1" ;;
  running) sudo -u mh -E .venv/bin/mh task list --status running --limit 200 ;;
  *) echo "usage: drain.sh on|off|running" >&2; exit 2 ;;
esac
