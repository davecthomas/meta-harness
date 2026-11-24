"""Says whether a pull request needs the two slow checks: the zero-cost end-to-end check and the sandbox's run
of this repo's checks.

Both prove the run path: the RunTask workflow, the sandbox image, the egress proxy, the services under them.
A change to the console's own source or to the docs cannot alter any of that, and the console's tests already
run in their own check, so for such a change the two slow checks say nothing new and cost four to six minutes
that a task's review then waits on.

A change that touches only documentation needs no check at all: no test, build or image reads those files.
So the answer has three values. Reads the changed files' paths on stdin, one a line, and prints `docs` when
every one is documentation (every check is skipped), `skip` when every one is the console's source or
documentation (the two slow checks are skipped), and `run` otherwise, an empty list included: when in doubt,
the checks run. A push to main never asks: every check runs there. The deploy waits for them unless
the merged files are exactly those the pull request's checks passed on (tested_already.py).
"""

import sys

# The console's source, and prose. Not the console's package files or build settings: a dependency or a build
# change is one the sandbox can meet differently.
SAFE_PREFIXES = ("console/src/", "docs/")
SAFE_FILES = ("README.md", "AGENTS.md")
# Prose no check reads. Not AGENTS.md: tests/planner/test_needs.py reads it.
DOCS_PREFIXES = ("docs/",)
DOCS_FILES = ("README.md",)


def needs_slow_checks(paths: list[str]) -> bool:
    """False only for a change that touches nothing but the console's source and the docs."""
    changed = [path.strip() for path in paths if path.strip()]
    return not changed or not all(path.startswith(SAFE_PREFIXES) or path in SAFE_FILES for path in changed)


def only_docs(paths: list[str]) -> bool:
    """True only for a change that touches nothing but documentation no check reads."""
    changed = [path.strip() for path in paths if path.strip()]
    return bool(changed) and all(path.startswith(DOCS_PREFIXES) or path in DOCS_FILES for path in changed)


def scope(paths: list[str]) -> str:
    """`docs`, `skip` or `run`, as the module docstring says."""
    if only_docs(paths):
        return "docs"
    return "run" if needs_slow_checks(paths) else "skip"


if __name__ == "__main__":
    print(scope(sys.stdin.read().splitlines()))
