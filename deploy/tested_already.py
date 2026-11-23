"""Says whether a commit's files already passed their checks on the pull request that merged them.

A deploy waits for main's checks at the merged commit. For a pull request that was up to date with main, the
merged commit holds exactly the files the pull request's last commit held, and the checks just passed on
them: running them again told the deploy nothing, and cost seven to eight minutes of every deploy. So the
deploy compares the two commits' trees, and when they are the same asks this whether the pull request's
checks passed. Main still runs its checks at the merged commit; the deploy does not wait for them.

Reads the pull request's last commit's check runs on stdin, as GitHub's API answers them
(`commits/<sha>/check-runs`), and prints `tested` only when every required check passed there. A slow check
the pull request skipped counts as passed only when it is one `checks_scope.py` may skip, which it does for a
change that cannot alter what the check proves. Anything else prints `wait`: when in doubt, the deploy waits.
"""

import json
import sys

# The checks a deploy waits for, as main runs them.
REQUIRED = (
    "control plane tests",
    "console web app",
    "zero-cost end-to-end check",
    "harness server tests",
    "sandbox runs this repo's checks",
)
# The two a pull request skips when it touches only the console's source or the docs (checks_scope.py).
MAY_SKIP = ("zero-cost end-to-end check", "sandbox runs this repo's checks")


def tested(check_runs: list[dict]) -> bool:
    """True only when each required check has run to an end on the commit and none ended any other way than
    passing, or, for one that may be skipped, being skipped."""
    for name in REQUIRED:
        runs = [run for run in check_runs if run.get("name") == name]
        if not runs or any(run.get("status") != "completed" for run in runs):
            return False
        allowed = {"success", "skipped"} if name in MAY_SKIP else {"success"}
        if any(run.get("conclusion") not in allowed for run in runs):
            return False
    return True


if __name__ == "__main__":
    try:
        found = json.load(sys.stdin).get("check_runs") or []
    except (ValueError, AttributeError):
        found = []
    print("tested" if tested(found) else "wait")
