"""Says whether a pull request needs the two slow checks: the zero-cost end-to-end check and the sandbox's run
of this repo's checks.

Both prove the run path: the RunTask workflow, the sandbox image, the egress proxy, the services under them.
A change to the console's own source or to the docs cannot alter any of that, and the console's tests already
run in their own check, so for such a change the two slow checks say nothing new and cost four to six minutes
that a task's review then waits on.

Reads the changed files' paths on stdin, one a line, and prints `skip` only when there is at least one and
every one is the console's source or documentation. Anything else, an empty list included, prints `run`: when
in doubt, the checks run. A push to main never asks: every check runs there. The deploy waits for them unless
the merged files are exactly those the pull request's checks passed on (tested_already.py).
"""

import sys

# The console's source, and prose. Not the console's package files or build settings: a dependency or a build
# change is one the sandbox can meet differently.
SAFE_PREFIXES = ("console/src/", "docs/")
SAFE_FILES = ("README.md", "AGENTS.md")


def needs_slow_checks(paths: list[str]) -> bool:
    """False only for a change that touches nothing but the console's source and the docs."""
    changed = [path.strip() for path in paths if path.strip()]
    return not changed or not all(path.startswith(SAFE_PREFIXES) or path in SAFE_FILES for path in changed)


if __name__ == "__main__":
    print("run" if needs_slow_checks(sys.stdin.read().splitlines()) else "skip")
