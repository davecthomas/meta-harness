"""An in-place update that leaves a service unhealthy goes back to the commit it replaced.

These run deploy/vm/services.sh in bash against two real git checkouts. systemctl, curl
and the commands that install dependencies are stand-ins: systemctl records each call, and
curl answers /health for every port except the one a test marks broken. The broken service
fails only while the new commit is checked out, as a service whose new code cannot start would.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "deploy" / "vm" / "services.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(path: Path, commits: int) -> list[str]:
    path.mkdir()
    _git("init", "--quiet", "--initial-branch=main", cwd=path)
    shas = []
    for n in range(commits):
        (path / "app.py").write_text(f"version = {n}\n")
        _git("add", ".", cwd=path)
        _git("commit", "--quiet", "-m", f"commit {n}", cwd=path)
        shas.append(_git("rev-parse", "HEAD", cwd=path))
    return shas


# The stand-ins. A bash function takes precedence over a command of the same name.
STUBS = r"""
log() { echo "[boot] $*"; }
sleep() { :; }
sudo() { echo "deps $(git -C "$MH" rev-parse HEAD)" >>"$CALLS"; }
install() { :; }
chown() { :; }
systemctl() {
  case "$1" in
    is-active) return 0 ;;
    restart) echo "restart $2 $(git -C "$MH" rev-parse HEAD)" >>"$CALLS" ;;
  esac
  return 0
}
curl() {
  local port=${2#http://127.0.0.1:}
  port=${port%%/*}
  [ "$port" != "$BROKEN_PORT" ] || { [ -z "$BROKEN_ALWAYS" ] && [ "$(git -C "$MH" rev-parse HEAD)" != "$NEW" ]; }
}
"""


def _update(tmp_path: Path, broken_port: str, broken_always: bool = False) -> tuple[int, list[str], str, str, str]:
    """Runs update_services for the commit after PREVIOUS, with the ERR trap boot.sh sets."""
    mh, gw = tmp_path / "mh", tmp_path / "gw"
    previous, new = _repo(mh, 2)
    _repo(gw, 1)
    calls = tmp_path / "calls"
    calls.touch()
    script = f"""
set -eEuo pipefail
MH={mh} GW={gw} PREVIOUS={previous} NEW={new} CALLS={calls} BROKEN_PORT={broken_port} BROKEN_ALWAYS={"yes" if broken_always else ""}
GW_PREVIOUS=$(git -C "$GW" rev-parse HEAD)
HEALTH_TRIES=2
source {SERVICES}
{STUBS}
trap rollback ERR
update_services
"""
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return run.returncode, calls.read_text().splitlines(), _git("rev-parse", "HEAD", cwd=mh), previous, new


def test_a_healthy_update_restarts_each_service_once_on_the_new_commit(tmp_path):
    code, calls, head, _previous, new = _update(tmp_path, broken_port="none")

    assert code == 0
    assert head == new
    restarts = [c for c in calls if c.startswith("restart mh-")]
    assert restarts == [
        f"restart {u} {new}" for u in ("mh-events", "mh-tasks", "mh-planner", "mh-scheduler", "mh-worker")
    ], "and never mh-migrate: restarting it stops every service that requires it, all at once"


def test_an_update_applies_migrations_beside_the_running_services_and_a_stop_never_waits_long():
    services = SERVICES.read_text()
    update = services[services.index("update_services() {") :]
    assert "apply_migrations" in update.split("local unit")[0] and "restart mh-migrate" not in services
    for unit in ("events", "tasks", "planner", "scheduler"):
        text = (ROOT / "deploy" / "vm" / "units" / f"mh-{unit}.service").read_text()
        assert "TimeoutStopSec=15" in text, f"mh-{unit} could hold a deploy for systemd's 90 seconds"
    cli = (ROOT / "src" / "meta_harness" / "cli.py").read_text()
    assert cli.count("uvicorn.run(") == cli.count("**SERVE") == 5


def test_a_service_that_fails_its_health_check_rolls_the_update_back(tmp_path):
    code, calls, head, previous, new = _update(tmp_path, broken_port="8303")

    assert code == 3, "3 tells the startup script the previous commit is back and healthy"
    assert head == previous
    restarts = [c for c in calls if c.startswith("restart mh-") and "migrate" not in c]
    # The update stopped at the planner: the scheduler and worker never ran the new commit.
    assert restarts[:3] == [f"restart {u} {new}" for u in ("mh-events", "mh-tasks", "mh-planner")]
    # The rollback restarted those three, in order, on the previous commit, after reinstalling its dependencies.
    assert restarts[3:] == [f"restart {u} {previous}" for u in ("mh-events", "mh-tasks", "mh-planner")]
    assert f"deps {previous}" in calls
    assert not any("mh-scheduler" in c or "mh-worker" in c for c in calls)


def test_a_rollback_that_cannot_make_the_service_healthy_says_so(tmp_path):
    code, _calls, head, previous, _new = _update(tmp_path, broken_port="8303", broken_always=True)

    assert code == 4, "4 tells the startup script a service is still down"
    assert head == previous


def test_an_update_with_no_previous_commit_does_not_roll_back(tmp_path):
    mh, gw = tmp_path / "mh", tmp_path / "gw"
    _repo(mh, 1)
    _repo(gw, 1)
    script = f"""
set -eEuo pipefail
MH={mh} GW={gw} PREVIOUS= GW_PREVIOUS=
source {SERVICES}
log() {{ echo "[boot] $*"; }}
trap rollback ERR
false
"""
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert run.returncode == 4
    assert "no previous commit" in run.stdout


def test_boot_sets_the_rollback_trap_for_an_update_only():
    boot = (ROOT / "deploy" / "vm" / "boot.sh").read_text()
    assert "set -eEuo pipefail" in boot, "-E carries the ERR trap into functions"
    assert '[ "$MODE" = boot ] || trap rollback ERR' in boot
    assert boot.index("trap rollback ERR") < boot.index('log "code at $REF')
    assert "update_services" in boot
    startup = (ROOT / "deploy" / "vm" / "startup.sh").read_text()
    assert 'report "failed $REF: rolled back to $MH_PREVIOUS_COMMIT"' in startup
