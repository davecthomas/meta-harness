"""Skills: packaged instructions, with the scripts and references they use, that a run's agent loads.

A skill is a directory in the usual shape: `SKILL.md`, whose frontmatter names the skill and says what it is for,
and the files beside it. The meta-harness keeps skills as a configuration kind, `skills`, so they are imported
once by an admin, versioned and rolled back like the rest, and available to every goal's runs. A harness profile
names the skills its runs load (`skills: [...]`); the run service sends their content with each run, and the
harness server loads exactly those, and nothing from the task's repo.

The document:

    {"skills": {"<name>": {"description": str, "source": {...}, "files": {"<path>": "<text>"}}}}

`source` says where the skill came from (`{"kind": "git", "url", "ref", "path", "commit"}`, `{"kind": "upload"}`
or `{"kind": "shipped"}`). Its content is stored with the version, so a run gets exactly what was approved,
whatever its source does later.

A locked skill is one the control plane itself depends on: it runs the skill's script in its own process, or
puts the skill's text in its prompts. So what the skill holds is decided by a pull request, never by a save.
config/skills/locked.json names the locked skills. Each is always the shipped files, with `"locked": true`
in its source: a save that changes or removes one is refused, an import under its name is refused, and a
deploy sets the stored copy to the shipped files (store.sync_locked). Every other skill is imported, changed
and removed as an admin sees fit, and runs only inside a per-run sandbox, which holds no credential.

This module checks a document, reads a skill from a directory or from a git repository, and lists what a
git repository offers an import: its branches and tags, and its directories that hold a SKILL.md. Nothing
here saves.
"""

import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from meta_harness.run_service.paths import REPO_ROOT

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# What one skill may hold. A skill is instructions and small scripts; anything bigger is not one.
MAX_FILES = 200
MAX_FILE_BYTES = 256 * 1024
MAX_SKILL_BYTES = 2 * 1024 * 1024
# Only https: a git URL may name a local path or another protocol, which the planner's process must not read.
GIT_URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:[0-9]+)?/[^\s]+$")
GIT_TIMEOUT_SECONDS = 60
# Directories never taken into a skill: version control and caches.
SKIPPED = {".git", "__pycache__", "node_modules", ".venv"}
# The file under config/skills/ that names the locked skills.
LOCKED_FILE = "locked.json"


class SkillError(ValueError):
    """A skill that cannot be read or imported. The message says why."""


def skills_dir() -> Path:
    """The shipped skills, config/skills/<name>/, which seed the kind's first version."""
    return Path(os.environ.get("MH_SKILLS_DIR") or REPO_ROOT / "config" / "skills")


def locked() -> set[str]:
    """The names of the locked skills: those config/skills/locked.json lists that ship as a directory there.
    Empty when the file is missing or cannot be read: `check_locked` stops a service that starts so."""
    root = skills_dir()
    try:
        names = json.loads((root / LOCKED_FILE).read_text(encoding="utf-8")).get("locked")
    except (OSError, ValueError, AttributeError):
        return set()
    if not isinstance(names, list):
        return set()
    return {name for name in names if isinstance(name, str) and (root / name / "SKILL.md").is_file()}


def check_locked(needed: list[str]) -> None:
    """Raises RuntimeError when config/skills/locked.json is unusable, or does not lock a skill the control
    plane depends on. needed: the names of those skills."""
    path = skills_dir() / LOCKED_FILE
    try:
        names = json.loads(path.read_text(encoding="utf-8")).get("locked")
    except (OSError, ValueError, AttributeError) as err:
        raise RuntimeError(f'{path}: cannot be read ({err}). It holds {{"locked": ["<skill name>", ...]}}') from err
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise RuntimeError(f'{path}: locked must be a list of skill names, such as {{"locked": ["{needed[0]}"]}}')
    absent = sorted(name for name in names if not (skills_dir() / name / "SKILL.md").is_file())
    if absent:
        raise RuntimeError(
            f"{path}: locks {', '.join(absent)}, which config/skills/ does not hold. Add the skill's directory "
            "there, or take the name out of the list"
        )
    unlocked = sorted(set(needed) - set(names))
    if unlocked:
        raise RuntimeError(
            f"{path}: {', '.join(unlocked)} must be locked, since the control plane runs its script and puts "
            "its text in prompts. Add the name to the list"
        )


def locked_errors(skills: dict[str, Any]) -> list[str]:
    """What a skills document does to the locked skills that it may not: change one, remove one, or call
    another skill locked."""
    names = locked()
    reasons = []
    if names:
        shipped = seed_skills()["skills"]
        for name in sorted(names):
            if skills.get(name) != shipped[name]:
                did = "changed" if name in skills else "removed"
                reasons.append(
                    f"{name} is locked and cannot be {did} here: the control plane runs its script and uses "
                    f"its text. Leave it as it is, and change it with a pull request to config/skills/{name}/"
                )
    for name, skill in skills.items():
        source = skill.get("source") if isinstance(skill, dict) else None
        if name not in names and isinstance(source, dict) and source.get("locked"):
            reasons.append(f"{name}: only a skill config/skills/{LOCKED_FILE} names is locked. Take locked out")
    return reasons


def frontmatter(text: str) -> dict[str, str]:
    """The SKILL.md frontmatter's top-level `key: value` lines, unquoted. Empty when there is none."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    found = {}
    for line in text[3:end].splitlines():
        key, colon, value = line.partition(":")
        if colon and key and not key.startswith((" ", "\t")):
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] == '"':
                # A double-quoted YAML string: its quotes go, and its escaped quotes are quotes.
                value = value[1:-1].replace('\\"', '"')
            found[key.strip()] = value.strip("'")
    return found


def _safe(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts and "\\" not in path


def skill_errors(name: str, skill: Any) -> list[str]:
    """What is wrong with one skill."""
    if not NAME.match(name):
        return [f"{name}: a skill's name is lowercase letters, digits and hyphens, at most 63"]
    if not isinstance(skill, dict):
        return [f"{name}: a skill is an object"]
    files = skill.get("files")
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return [f"{name}: files must map each path to its text"]
    reasons = []
    if "SKILL.md" not in files:
        reasons.append(f"{name}: a skill has a SKILL.md")
    else:
        head = frontmatter(files["SKILL.md"])
        if head.get("name") != name:
            reasons.append(f"{name}: its SKILL.md names it {head.get('name')!r}; the two must match")
        if not head.get("description"):
            reasons.append(f"{name}: its SKILL.md has no description, which is how an agent knows when to use it")
    if not isinstance(skill.get("description"), str) or not skill.get("description"):
        reasons.append(f"{name}: description is required")
    if len(files) > MAX_FILES:
        reasons.append(f"{name}: {len(files)} files is more than a skill may hold ({MAX_FILES})")
    unsafe = sorted(path for path in files if not _safe(path))
    if unsafe:
        reasons.append(f"{name}: a file's path is relative and stays inside the skill: {', '.join(unsafe[:5])}")
    big = sorted(path for path, text in files.items() if len(text.encode()) > MAX_FILE_BYTES)
    if big:
        reasons.append(f"{name}: files over {MAX_FILE_BYTES // 1024} KB: {', '.join(big[:5])}")
    total = sum(len(text.encode()) for text in files.values())
    if total > MAX_SKILL_BYTES:
        reasons.append(
            f"{name}: {total // 1024} KB in all is more than a skill may hold ({MAX_SKILL_BYTES // 1024} KB)"
        )
    return reasons


def check_skills(document: dict[str, Any], others: dict[str, dict[str, Any]]) -> list[str]:
    """Problems with a skills document, given the other kinds' active documents: a skill a harness profile
    names cannot go."""
    skills = document.get("skills")
    if not isinstance(skills, dict):
        return ["skills must be an object of skills, by name"]
    reasons = []
    for name, skill in skills.items():
        reasons += skill_errors(name, skill)
    reasons += locked_errors(skills)
    profiles = (others.get("profiles") or {}).get("profiles", {})
    for profile, spec in profiles.items():
        for name in (spec or {}).get("skills") or []:
            if name not in skills:
                reasons.append(f"harness profile {profile} names skill {name}: remove it from the profile first")
    return reasons


def read_dir(root: Path) -> dict[str, str]:
    """Every text file under a skill's directory, by its path relative to it.

    Raises:
        SkillError: A file is not text, or the directory holds more than a skill may.
    """
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIPPED for part in relative.parts) or path.is_symlink() or not path.is_file():
            continue
        if len(files) >= MAX_FILES:
            raise SkillError(f"{root.name} holds more than {MAX_FILES} files, more than a skill may")
        try:
            files[relative.as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as err:
            raise SkillError(f"{relative.as_posix()} is not text: a skill holds text files only") from err
    return files


def from_files(files: dict[str, str], source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A skill from its files: its name and its entry for the document.

    Raises:
        SkillError: There is no SKILL.md, or it names no skill.
    """
    if "SKILL.md" not in files:
        raise SkillError("no SKILL.md: a skill's directory has one at its top")
    head = frontmatter(files["SKILL.md"])
    name = head.get("name") or ""
    if not name:
        raise SkillError("its SKILL.md names no skill (a `name:` line in its frontmatter)")
    skill = {"description": head.get("description") or "", "source": source, "files": files}
    reasons = skill_errors(name, skill)
    if reasons:
        raise SkillError("; ".join(reasons))
    return name, skill


def seed_skills() -> dict[str, Any]:
    """The shipped skills, each a directory under config/skills/, as the kind's first version."""
    found: dict[str, Any] = {}
    root = skills_dir()
    if root.is_dir():
        names = locked()
        for directory in sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
            name, skill = from_files(read_dir(directory), {"kind": "shipped"})
            if name in names:
                skill["source"] = {"kind": "shipped", "locked": True}
            found[name] = skill
    return {"skills": found}


def _private_address(host: str) -> str | None:
    """An address the host's name resolves to that is not a public one (private, loopback, link-local, such as
    the cloud's metadata server), or None. A name that does not resolve is left for git to report."""
    try:
        found = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for *_, sockaddr in found:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            return str(address)
    return None


def _check_url(url: str) -> None:
    """Refuses a git URL that is not https on a public host by name.

    Raises:
        SkillError: The URL is not https, or its host is an address, a private name or resolves inside a network.
    """
    host = url.split("/")[2].split(":")[0].lower() if GIT_URL.match(url) else ""
    # A public git host by name: never an address, this machine or a private network's name.
    private = not re.search(r"[a-z]", host) or "." not in host or host.endswith((".internal", ".local", ".localhost"))
    if not host or private:
        raise SkillError("a skill is imported from an https:// git URL on a public host")
    if inside := _private_address(host):
        raise SkillError(
            f"{host} is at {inside}, an address inside a private network: a skill is imported from a public "
            "git host, such as github.com"
        )


def _git(command: list[str], url: str, ref: str | None) -> str:
    """A git command's output. It never asks for a password and never follows a redirect: a public host must
    not send it on to an address inside the network.

    Raises:
        SkillError: git failed or took too long; the message says which, with git's last line.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    env |= {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.followRedirects", "GIT_CONFIG_VALUE_0": "false"}
    try:
        return subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, env=env
        ).stdout
    except subprocess.TimeoutExpired as err:
        raise SkillError(f"reading {url} took longer than {GIT_TIMEOUT_SECONDS} seconds") from err
    except subprocess.CalledProcessError as err:
        reason = (err.stderr or "").strip().splitlines()[-1:] or ["git failed"]
        raise SkillError(f"could not read {url}{f' at {ref}' if ref else ''}: {reason[0]}") from err


def _clone(url: str, ref: str | None, clone: Path, *, files: bool) -> None:
    """A shallow clone of one branch or tag. files=False takes the tree without the files' contents."""
    command = ["git", "clone", "--quiet", "--depth", "1", "--no-tags", "--single-branch"]
    command += [] if files else ["--filter=blob:none", "--no-checkout"]
    command += ["--branch", ref] if ref else []
    _git([*command, "--", url, str(clone)], url, ref)


def from_git(url: str, ref: str | None, path: str | None) -> tuple[str, dict[str, Any]]:
    """A skill read from a directory of a git repository at a branch, tag or commit, as of now. The commit it
    was read at is kept in its source, so the import says exactly what was taken.

    Raises:
        SkillError: The URL is not https, the repository or ref cannot be read, or the path holds no skill.
    """
    _check_url(url)
    if path and not _safe(path):
        raise SkillError("the path is relative to the repository and stays inside it")
    with tempfile.TemporaryDirectory(prefix="mh-skill-") as work:
        clone = Path(work) / "repo"
        _clone(url, ref, clone, files=True)
        commit = _git(["git", "-C", str(clone), "rev-parse", "HEAD"], url, ref).strip()
        root = (clone / path) if path else clone
        if not (root / "SKILL.md").is_file():
            raise SkillError(f"{path or 'the repository top'} holds no SKILL.md")
        source = {"kind": "git", "url": url, "ref": ref or "", "path": path or "", "commit": commit}
        return from_files(read_dir(root), source)


def git_source(url: str, ref: str | None) -> dict[str, Any]:
    """What a git repository offers an import, as of now: its branches and tags, its default branch, and the
    directories that hold a SKILL.md at ref (the default branch when None), by their path in the repository.
    A SKILL.md at the top is not listed, since an empty path reads it.
    Every one is listed: a list cut short would make the import refuse a name the repository has.

    Raises:
        SkillError: The URL is not https on a public host, the repository cannot be read, or it has no such ref.
    """
    _check_url(url)
    listed = _git(["git", "ls-remote", "--symref", "--", url, "HEAD", "refs/heads/*", "refs/tags/*"], url, None)
    branches: set[str] = set()
    tags: set[str] = set()
    default = None
    for line in listed.splitlines():
        head, _, name = line.partition("\t")
        if head.startswith("ref: refs/heads/") and name == "HEAD":
            default = head.removeprefix("ref: refs/heads/")
        elif name.startswith("refs/heads/"):
            branches.add(name.removeprefix("refs/heads/"))
        elif name.startswith("refs/tags/"):
            tags.add(name.removeprefix("refs/tags/").removesuffix("^{}"))
    ref = ref or None
    if ref and ref not in branches and ref not in tags:
        raise SkillError(f"{url} has no branch or tag named {ref}")
    paths: list[str] = []
    if branches or tags:
        with tempfile.TemporaryDirectory(prefix="mh-skill-") as work:
            clone = Path(work) / "repo"
            _clone(url, ref, clone, files=False)
            tree = _git(["git", "-C", str(clone), "ls-tree", "-r", "-z", "--name-only", "HEAD"], url, ref)
        for name in tree.split("\0"):
            parts = PurePosixPath(name).parts
            if len(parts) > 1 and parts[-1] == "SKILL.md" and not SKIPPED.intersection(parts):
                paths.append("/".join(parts[:-1]))
    return {
        "branches": sorted(branches),
        "tags": sorted(tags),
        "default_branch": default,
        "ref": ref or default,
        "paths": sorted(paths),
    }


def for_run(skills: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    """The skills a run loads, as the harness server takes them: each one's name and files. A name the
    document lacks is left out; the profile check keeps that from happening."""
    return [{"name": name, "files": skills[name]["files"]} for name in names if name in skills]
