"""Which local bare repos a task may name.

A task's repo is `owner/name` on a code host, or the path of a local bare git
repo, which the tests, the zero-cost end-to-end check and the deploy's smoke
task use. The run service clones a local repo and pushes to it on the host,
and a push runs the destination repo's hooks there, outside any sandbox. So a
path is taken only when the deployment vouches for it: the smoke repo
(`MH_SMOKE_REPO`), or a repo under a directory listed in
`MH_LOCAL_REPO_ROOTS`. A production deployment sets no roots, so a task
cannot name a repo that an earlier run's agent built in its workspace.
"""

import os
from pathlib import Path

ROOTS_ENV = "MH_LOCAL_REPO_ROOTS"
SMOKE_ENV = "MH_SMOKE_REPO"
SMOKE_DEFAULT = "/var/lib/meta-harness/smoke/origin.git"


def is_local(repo: str) -> bool:
    """Whether a repo is named as a path on this machine, not as owner/name on a code host."""
    return repo.startswith("/")


def smoke_repo() -> Path:
    """The bare repo the deploy's smoke task works in."""
    return Path(os.environ.get(SMOKE_ENV) or SMOKE_DEFAULT)


def roots() -> list[Path]:
    """The directories whose repos a task may name, from MH_LOCAL_REPO_ROOTS. Raises RuntimeError naming a
    bad entry."""
    found = []
    for entry in (os.environ.get(ROOTS_ENV) or "").split(os.pathsep):
        if not entry.strip():
            continue
        path = Path(entry.strip())
        if not path.is_absolute():
            raise RuntimeError(f"{ROOTS_ENV}: {entry!r} is not an absolute path; write each directory in full")
        if path == Path(path.anchor):
            raise RuntimeError(f"{ROOTS_ENV}: {entry!r} would allow every path; name the directory the repos are in")
        found.append(path)
    return found


def check_settings() -> None:
    """Raises RuntimeError naming what is wrong with the local repo settings."""
    roots()
    if not smoke_repo().is_absolute():
        raise RuntimeError(f"{SMOKE_ENV}: {str(smoke_repo())!r} is not an absolute path; write it in full")


def allowed(repo: str) -> bool:
    """Whether a local repo is one the deployment vouches for. Symlinks are followed first, so a link under
    an allowed directory cannot point a task at a repo outside it."""
    try:
        path = Path(repo).resolve()
        if path == smoke_repo().resolve():
            return True
        return any(path != root.resolve() and path.is_relative_to(root.resolve()) for root in roots())
    except (OSError, RuntimeError):
        return False


def refusal(repo: str) -> str:
    """What to tell whoever named a local repo that is not allowed, with what to do about it."""
    return (
        f"{repo} is a local path this deployment does not take as a repo. Name the repo as owner/name, or, "
        f"for a test or a local run, put its directory in {ROOTS_ENV}"
    )
