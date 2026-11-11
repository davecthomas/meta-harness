"""The version of a commit on main, worked out from main's history instead of written by each pull request.

    python3 src/meta_harness/versioning.py            the version at HEAD
    python3 src/meta_harness/versioning.py --check origin/main
                                                      exit 1 when HEAD changes a version file main did not

Every pull request used to bump the version in five files. With tasks in parallel, every pair of open pull
requests conflicted there, each conflict cost a fix run, and reviewers read the bump as a change outside the
issue's paths and had it reverted. So no pull request touches the version now.

The file VERSION holds a base version. The version of a commit is that base, bumped once for each commit on
main's first-parent line since the commit that last changed VERSION: a minor bump for a feature, a patch bump
for a fix, none for a change that ships nothing. A commit says which it is with a `Version: fix|feature|none`
line in its message, which the run service writes when it merges a task's pull request; without one, its
subject's prefix decides (`feat/` or `feat:`, `fix/` or `fix:`, and `chore`, `docs`, `test` or `ci` for
none), and a commit that says nothing counts as a fix.

The deploy works the version out and gives it to the running services as MH_VERSION. pyproject.toml, the
package's own __version__, console/package.json and the lock files hold the base, and change only with VERSION.

This file uses the standard library only, so the deploy runs it before anything is installed.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("fix", "feature", "none")
TRAILER = re.compile(r"^Version:\s*(fix|feature|none)\s*$", re.IGNORECASE | re.MULTILINE)
PREFIXES = (
    (re.compile(r"^feat(ure)?[/:(]", re.IGNORECASE), "feature"),
    (re.compile(r"^fix[/:(]", re.IGNORECASE), "fix"),
    (re.compile(r"^(chore|docs?|tests?|ci)[/:(]", re.IGNORECASE), "none"),
)
# The files that hold the base version, and how each one's version line reads. A pull request changes none.
VERSION_LINES = {
    "VERSION": re.compile(r"^(\d+\.\d+\.\d+)\s*$", re.MULTILINE),
    "pyproject.toml": re.compile(r'^version = "([^"]+)"', re.MULTILINE),
    "src/meta_harness/__init__.py": re.compile(r'^BASE_VERSION = "([^"]+)"', re.MULTILINE),
    "console/package.json": re.compile(r'^  "version": "([^"]+)"', re.MULTILINE),
    "console/package-lock.json": re.compile(r'^  "version": "([^"]+)"', re.MULTILINE),
}


def kind_of(subject: str, body: str = "") -> str:
    """What kind of change a commit is, for its version bump: fix, feature or none."""
    named = TRAILER.findall(body)
    if named:
        return named[-1].lower()
    for pattern, kind in PREFIXES:
        if pattern.match(subject.strip()):
            return kind
    return "fix"


def bump(version: str, kind: str) -> str:
    """The version after one change of this kind."""
    major, minor, patch = (int(part) for part in version.split("."))
    if kind == "feature":
        return f"{major}.{minor + 1}.0"
    if kind == "fix":
        return f"{major}.{minor}.{patch + 1}"
    return version


def after(base: str, commits: list[tuple[str, str]]) -> str:
    """The base version bumped once per commit, oldest first. Each commit is (subject, body)."""
    version = base
    for subject, body in commits:
        version = bump(version, kind_of(subject, body))
    return version


def base_version(root: Path = ROOT) -> str:
    return (root / "VERSION").read_text(encoding="utf-8").strip()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def current(root: Path = ROOT, ref: str = "HEAD") -> str:
    """The version at ref: the base, bumped for each first-parent commit since VERSION last changed. Where
    there is no git history to read (an image built from a copy of the files), the base."""
    base = base_version(root)
    try:
        since = _git(root, "log", "-1", "--format=%H", ref, "--", "VERSION").strip()
        if not since:
            return base
        log = _git(root, "log", "--first-parent", "--reverse", "--format=%s%x1f%b%x1e", f"{since}..{ref}")
    except (OSError, subprocess.CalledProcessError):
        return base
    commits = []
    for entry in log.split("\x1e"):
        if entry.strip():
            subject, _, body = entry.strip("\n").partition("\x1f")
            commits.append((subject, body))
    return after(base, commits)


def version_changes(root: Path, base_ref: str) -> list[str]:
    """The version files whose version line differs between base_ref and HEAD. A pull request has none, unless
    it re-bases the version on purpose: then VERSION changes too, and every file holds the same new base."""

    def line(ref: str, path: str) -> str | None:
        try:
            text = _git(root, "show", f"{ref}:{path}")
        except subprocess.CalledProcessError:
            return None
        found = VERSION_LINES[path].search(text)
        return found.group(1) if found else None

    changed = [path for path in VERSION_LINES if line(base_ref, path) != line("HEAD", path)]
    if "VERSION" in changed and len({line("HEAD", path) for path in VERSION_LINES}) == 1:
        return []
    return changed


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        changed = version_changes(ROOT, argv[1])
        if changed:
            print(
                f"this change sets the version in {', '.join(changed)}. A pull request does not: the version is "
                "worked out when it merges (src/meta_harness/versioning.py). Put the files back as "
                f"{argv[1]} has them.",
                file=sys.stderr,
            )
            return 1
        return 0
    print(current(ROOT, argv[0] if argv else "HEAD"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
